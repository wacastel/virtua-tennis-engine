// Reference laboratory only. Loads an explicitly selected libretro core.
// This file is never part of the shipping app or its fixed CPU engine.
#import <Foundation/Foundation.h>
#import <ImageIO/ImageIO.h>
#import <OpenGL/OpenGL.h>
#import <OpenGL/gl3.h>
#include <CommonCrypto/CommonDigest.h>
#include <libretro.h>
#include <dlfcn.h>
#include <algorithm>
#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;
static constexpr uint32_t validMask = 0x3bfbf;
static constexpr unsigned surfaceSize = 2048;
static CGLContextObj context;
static GLuint framebuffer, color, depth;
static retro_hw_render_callback hardware{};
static retro_system_av_info av{};
static std::string systemPath, savePath, failure;
static std::map<std::string, std::string> options;
static std::set<unsigned> unknownEnvironment;
static std::map<unsigned, uint64_t> coreGLErrors;
static uint32_t buttons;
static unsigned pictureWidth, pictureHeight, videoCalls;
static bool shutdownRequested, gotFrame, duplicateFrame;
static std::vector<uint8_t> picture;
static std::vector<int16_t> pcm;
static uint64_t totalSamples;
static CC_SHA256_CTX audioStream;

static void require(bool ok, const std::string& reason) {
    if (!ok) throw std::runtime_error(reason);
}
static std::string digest(const void* data, size_t size) {
    CC_SHA256_CTX c; CC_SHA256_Init(&c);
    if (size) CC_SHA256_Update(&c, data, (CC_LONG)size);
    unsigned char hash[CC_SHA256_DIGEST_LENGTH]; CC_SHA256_Final(hash, &c);
    char text[65];
    for (unsigned i = 0; i < sizeof hash; ++i) snprintf(text + i * 2, 3, "%02x", hash[i]);
    return text;
}
static std::string fileDigest(const fs::path& path) {
    std::ifstream file(path, std::ios::binary); require(file.good(), "Cannot hash " + path.string());
    CC_SHA256_CTX c; CC_SHA256_Init(&c); char bytes[65536];
    while (file) { file.read(bytes, sizeof bytes); if (file.gcount()) CC_SHA256_Update(&c, bytes, (CC_LONG)file.gcount()); }
    require(file.eof(), "Hash read failed: " + path.string());
    unsigned char hash[32]; CC_SHA256_Final(hash, &c); char text[65];
    for (unsigned i = 0; i < 32; ++i) snprintf(text + i * 2, 3, "%02x", hash[i]);
    return text;
}
static void logMessage(enum retro_log_level level, const char* fmt, ...) {
    fprintf(stderr, "[core:%d] ", level);
    va_list args; va_start(args, fmt); vfprintf(stderr, fmt, args); va_end(args);
}
static uintptr_t currentFramebuffer() { return framebuffer; }
static retro_proc_address_t procedure(const char* name) {
    return reinterpret_cast<retro_proc_address_t>(dlsym(RTLD_DEFAULT, name));
}
static void createGL() {
    CGLPixelFormatAttribute attributes[] = {
        kCGLPFAOpenGLProfile, (CGLPixelFormatAttribute)kCGLOGLPVersion_3_2_Core,
        kCGLPFAAccelerated, kCGLPFAColorSize, (CGLPixelFormatAttribute)32,
        (CGLPixelFormatAttribute)0
    };
    CGLPixelFormatObj format = nullptr; GLint count = 0;
    require(CGLChoosePixelFormat(attributes, &format, &count) == kCGLNoError && count,
            "No accelerated macOS OpenGL 3.2 core format");
    CGLError result = CGLCreateContext(format, nullptr, &context);
    CGLDestroyPixelFormat(format);
    require(result == kCGLNoError && context, "CGLCreateContext failed");
    require(CGLSetCurrentContext(context) == kCGLNoError, "Cannot make CGL context current");
    glGenFramebuffers(1, &framebuffer); glBindFramebuffer(GL_FRAMEBUFFER, framebuffer);
    glGenTextures(1, &color); glBindTexture(GL_TEXTURE_2D, color);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8, surfaceSize, surfaceSize, 0, GL_RGBA, GL_UNSIGNED_BYTE, nullptr);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, color, 0);
    glGenRenderbuffers(1, &depth); glBindRenderbuffer(GL_RENDERBUFFER, depth);
    glRenderbufferStorage(GL_RENDERBUFFER, GL_DEPTH24_STENCIL8, surfaceSize, surfaceSize);
    glFramebufferRenderbuffer(GL_FRAMEBUFFER, GL_DEPTH_STENCIL_ATTACHMENT, GL_RENDERBUFFER, depth);
    require(glCheckFramebufferStatus(GL_FRAMEBUFFER) == GL_FRAMEBUFFER_COMPLETE, "Incomplete reference framebuffer");
    glClearColor(0, 0, 0, 1); glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT | GL_STENCIL_BUFFER_BIT);
}
static void destroyGL() {
    if (!context) return;
    glDeleteRenderbuffers(1, &depth); glDeleteTextures(1, &color); glDeleteFramebuffers(1, &framebuffer);
    CGLSetCurrentContext(nullptr); CGLDestroyContext(context); context = nullptr;
}
static bool environment(unsigned command, void* data) {
    switch (command) {
        case RETRO_ENVIRONMENT_GET_LOG_INTERFACE:
            static_cast<retro_log_callback*>(data)->log = logMessage; return true;
        case RETRO_ENVIRONMENT_GET_SYSTEM_DIRECTORY:
            *static_cast<const char**>(data) = systemPath.c_str(); return true;
        case RETRO_ENVIRONMENT_GET_SAVE_DIRECTORY:
            *static_cast<const char**>(data) = savePath.c_str(); return true;
        case RETRO_ENVIRONMENT_GET_CORE_OPTIONS_VERSION:
            *static_cast<unsigned*>(data) = 2; return true;
        case RETRO_ENVIRONMENT_GET_LANGUAGE:
            *static_cast<unsigned*>(data) = RETRO_LANGUAGE_ENGLISH; return true;
        case RETRO_ENVIRONMENT_SET_CORE_OPTIONS_V2_INTL: {
            auto d = static_cast<retro_core_options_v2_intl*>(data)->us->definitions;
            for (; d && d->key; ++d) options.try_emplace(d->key, d->default_value ? d->default_value : "");
            return true;
        }
        case RETRO_ENVIRONMENT_SET_CORE_OPTIONS_V2: {
            auto d = static_cast<retro_core_options_v2*>(data)->definitions;
            for (; d && d->key; ++d) options.try_emplace(d->key, d->default_value ? d->default_value : "");
            return true;
        }
        case RETRO_ENVIRONMENT_GET_VARIABLE: {
            auto v = static_cast<retro_variable*>(data); auto it = options.find(v->key);
            v->value = it == options.end() ? nullptr : it->second.c_str(); return v->value != nullptr;
        }
        case RETRO_ENVIRONMENT_GET_VARIABLE_UPDATE:
            *static_cast<bool*>(data) = false; return true;
        case RETRO_ENVIRONMENT_GET_INPUT_BITMASKS: return true;
        case RETRO_ENVIRONMENT_GET_CAN_DUPE:
            *static_cast<bool*>(data) = true; return true;
        case RETRO_ENVIRONMENT_GET_FASTFORWARDING:
            *static_cast<bool*>(data) = false; return true;
        case RETRO_ENVIRONMENT_GET_AUDIO_VIDEO_ENABLE:
            *static_cast<int*>(data) = 3; return true;
        case RETRO_ENVIRONMENT_SET_PIXEL_FORMAT:
            return *static_cast<unsigned*>(data) == RETRO_PIXEL_FORMAT_XRGB8888;
        case RETRO_ENVIRONMENT_GET_PREFERRED_HW_RENDER:
            *static_cast<unsigned*>(data) = RETRO_HW_CONTEXT_OPENGL_CORE; return true;
        case RETRO_ENVIRONMENT_SET_HW_RENDER: {
            auto h = static_cast<retro_hw_render_callback*>(data);
            if (h->context_type != RETRO_HW_CONTEXT_OPENGL_CORE || h->version_major > 3 ||
                (h->version_major == 3 && h->version_minor > 2)) return false;
            h->get_current_framebuffer = currentFramebuffer; h->get_proc_address = procedure;
            hardware = *h; return true;
        }
        case RETRO_ENVIRONMENT_SET_GEOMETRY:
            av.geometry = *static_cast<retro_game_geometry*>(data); return true;
        case RETRO_ENVIRONMENT_SET_SYSTEM_AV_INFO:
            av = *static_cast<retro_system_av_info*>(data); return true;
        case RETRO_ENVIRONMENT_SET_MESSAGE: {
            auto m = static_cast<retro_message*>(data); fprintf(stderr, "[message] %s\n", m->msg); return true;
        }
        case RETRO_ENVIRONMENT_SHUTDOWN: shutdownRequested = true; return true;
        case RETRO_ENVIRONMENT_SET_ROTATION:
            if (*static_cast<unsigned*>(data)) { failure = "Unexpected rotated Virtua Tennis display"; return false; }
            return true;
        case RETRO_ENVIRONMENT_SET_INPUT_DESCRIPTORS:
        case RETRO_ENVIRONMENT_SET_CONTROLLER_INFO:
        case RETRO_ENVIRONMENT_SET_SUPPORT_NO_GAME:
        case RETRO_ENVIRONMENT_SET_CORE_OPTIONS_DISPLAY:
        case RETRO_ENVIRONMENT_SET_CORE_OPTIONS_UPDATE_DISPLAY_CALLBACK:
        case RETRO_ENVIRONMENT_SET_PERFORMANCE_LEVEL: return true;
        default: unknownEnvironment.insert(command); return false;
    }
}
static void pollInput() {}
static int16_t input(unsigned port, unsigned device, unsigned index, unsigned id) {
    if (port > 1 || device != RETRO_DEVICE_JOYPAD || index != 0) return 0;
    uint32_t p = (buttons >> (port * 8)) & 255;
    uint16_t mask = 0;
    if (p & 1) mask |= 1 << RETRO_DEVICE_ID_JOYPAD_UP;
    if (p & 2) mask |= 1 << RETRO_DEVICE_ID_JOYPAD_DOWN;
    if (p & 4) mask |= 1 << RETRO_DEVICE_ID_JOYPAD_LEFT;
    if (p & 8) mask |= 1 << RETRO_DEVICE_ID_JOYPAD_RIGHT;
    if (p & 16) mask |= 1 << RETRO_DEVICE_ID_JOYPAD_B; // NAOMI SW1 / Shot
    if (p & 32) mask |= 1 << RETRO_DEVICE_ID_JOYPAD_A; // NAOMI SW2 / Lob
    if (p & 128) mask |= 1 << RETRO_DEVICE_ID_JOYPAD_START;
    if (buttons & (1U << (16 + port))) mask |= 1 << RETRO_DEVICE_ID_JOYPAD_SELECT;
    return id == RETRO_DEVICE_ID_JOYPAD_MASK ? (int16_t)mask : id < 16 && (mask & (1 << id)) ? 1 : 0;
}
static void video(const void* data, unsigned width, unsigned height, size_t) {
    ++videoCalls;
    if (!data) { duplicateFrame = true; return; }
    if (data != RETRO_HW_FRAME_BUFFER_VALID || !width || !height || width > surfaceSize || height > surfaceSize) {
        failure = "Unexpected video format/dimensions"; return;
    }
    pictureWidth = width; pictureHeight = height; picture.resize(size_t(width) * height * 4);
    for (GLenum e; (e = glGetError()) != GL_NO_ERROR;) ++coreGLErrors[e];
    GLint oldRead = 0, oldPack = 0; glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING, &oldRead);
    glGetIntegerv(GL_PIXEL_PACK_BUFFER_BINDING, &oldPack); glBindBuffer(GL_PIXEL_PACK_BUFFER, 0);
    glBindFramebuffer(GL_READ_FRAMEBUFFER, framebuffer); glReadBuffer(GL_COLOR_ATTACHMENT0);
    glPixelStorei(GL_PACK_ALIGNMENT, 1); glPixelStorei(GL_PACK_ROW_LENGTH, 0);
    glPixelStorei(GL_PACK_SKIP_PIXELS, 0); glPixelStorei(GL_PACK_SKIP_ROWS, 0);
    glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, picture.data());
    glBindFramebuffer(GL_READ_FRAMEBUFFER, oldRead);
    glBindBuffer(GL_PIXEL_PACK_BUFFER, oldPack);
    if (hardware.bottom_left_origin) {
        size_t stride = size_t(width) * 4;
        for (unsigned y = 0; y < height / 2; ++y)
            std::swap_ranges(picture.begin() + y * stride, picture.begin() + (y + 1) * stride,
                             picture.begin() + (height - y - 1) * stride);
    }
    if (glGetError() != GL_NO_ERROR) failure = "OpenGL readback error";
    gotFrame = true;
}
static size_t audio(const int16_t* data, size_t frames) {
    if (frames > 44100 * 2 || pcm.size() + frames * 2 > 44100 * 4) {
        failure = "Unexpected audio batch bounds"; return frames;
    }
    pcm.insert(pcm.end(), data, data + frames * 2); return frames;
}
static void audioOne(int16_t left, int16_t right) { int16_t p[2] = {left, right}; audio(p, 1); }
static void writeJSON(const fs::path& path, NSDictionary* value) {
    NSError* error = nil;
    NSData* data = [NSJSONSerialization dataWithJSONObject:value options:NSJSONWritingPrettyPrinted | NSJSONWritingSortedKeys error:&error];
    require(data && [data writeToFile:@(path.c_str()) atomically:YES], "Cannot write JSON report");
}
static void savePNG(const fs::path& path) {
    if (picture.empty()) return;
    CGDataProviderRef provider = CGDataProviderCreateWithData(nullptr, picture.data(), picture.size(), nullptr);
    CGColorSpaceRef colors = CGColorSpaceCreateDeviceRGB();
    CGImageRef image = CGImageCreate(pictureWidth, pictureHeight, 8, 32, pictureWidth * 4, colors,
                                    kCGBitmapByteOrderDefault | kCGImageAlphaNoneSkipLast, provider, nullptr, false, kCGRenderingIntentDefault);
    NSURL* url = [NSURL fileURLWithPath:@(path.c_str())];
    CGImageDestinationRef destination = CGImageDestinationCreateWithURL((__bridge CFURLRef)url, CFSTR("public.png"), 1, nullptr);
    require(destination && image, "Cannot create PNG");
    CGImageDestinationAddImage(destination, image, nullptr);
    bool saved = CGImageDestinationFinalize(destination);
    CFRelease(destination); CGImageRelease(image); CGColorSpaceRelease(colors); CGDataProviderRelease(provider);
    require(saved, "Cannot finalize PNG");
}
template<typename T> static T symbol(void* library, const char* name) {
    void* p = dlsym(library, name); require(p != nullptr, std::string("Missing core export: ") + name);
    return reinterpret_cast<T>(p);
}

int main(int argc, char** argv) { @autoreleasepool {
    void* library = nullptr; bool initialized = false, loaded = false, resetContext = false;
    void (*unload)() = nullptr; void (*deinit)() = nullptr;
    try {
        std::map<std::string, std::string> args;
        for (int i = 1; i < argc; ++i) {
            std::string key = argv[i];
            if (key == "--self-test") { args[key] = "1"; continue; }
            require(key.rfind("--", 0) == 0 && i + 1 < argc, "Each argument requires a value");
            require(args.emplace(key, argv[++i]).second, "Duplicate argument: " + key);
        }
        // Match the app's intro framebuffer feedback. The laboratory can
        // disable readback to reproduce the former flat-grey scenes.
        const std::string rttReadback = args.count("--rtt-readback") ? args.at("--rtt-readback") : "enabled";
        require(rttReadback == "enabled" || rttReadback == "disabled",
                "--rtt-readback must be enabled or disabled");
        createGL();
        if (args.count("--self-test")) {
            glClearColor(0.25, 0.5, 0.75, 1); glClear(GL_COLOR_BUFFER_BIT);
            uint8_t pixel[4]; glReadPixels(0, 0, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, pixel);
            require(pixel[0] == 64 && pixel[1] == 128 && pixel[2] == 191 && pixel[3] == 255, "CGL color readback mismatch");
            glBindVertexArray(0); glDisableVertexAttribArray(0);
            require(glGetError() == GL_INVALID_OPERATION, "Unexpected core-profile VAO-zero behavior");
            for (unsigned port = 0; port < 2; ++port) for (unsigned b : {1,2,4,8,16,32,128}) {
                buttons = b << (port * 8);
                require(input(port, RETRO_DEVICE_JOYPAD, 0, RETRO_DEVICE_ID_JOYPAD_MASK) != 0 &&
                        input(1 - port, RETRO_DEVICE_JOYPAD, 0, RETRO_DEVICE_ID_JOYPAD_MASK) == 0, "Player input isolation failed");
            }
            for (unsigned port = 0; port < 2; ++port) {
                buttons = 1 << (16 + port);
                require(input(port, RETRO_DEVICE_JOYPAD, 0, RETRO_DEVICE_ID_JOYPAD_SELECT) == 1 &&
                        input(1-port, RETRO_DEVICE_JOYPAD, 0, RETRO_DEVICE_ID_JOYPAD_SELECT) == 0, "Coin isolation failed");
            }
            printf("Reference harness CGL/readback and 16 input isolation checks passed; no game engine loaded.\n");
            destroyGL(); return 0;
        }
        for (auto key : {"--core", "--content", "--system", "--saves", "--out"}) require(args.count(key), std::string("Missing ") + key);
        for (const auto& [key, value] : args)
            require(key == "--core" || key == "--content" || key == "--system" || key == "--saves" || key == "--out" || key == "--frames" || key == "--route" || key == "--capture-every" || key == "--rtt-readback", "Unknown argument " + key);
        systemPath = fs::absolute(args.at("--system")).string(); savePath = fs::absolute(args.at("--saves")).string();
        require(fs::is_directory(systemPath), "System directory does not exist");
        for (auto key : {"--saves", "--out"}) {
            fs::path p = fs::absolute(args.at(key));
            require(!fs::exists(p) || (fs::is_directory(p) && fs::is_empty(p)), "Use an empty isolated directory: " + p.string());
            fs::create_directories(p);
        }
        fs::path output = fs::absolute(args.at("--out"));
        std::vector<uint32_t> route;
        if (args.count("--route")) {
            NSData* bytes = [NSData dataWithContentsOfFile:@(args.at("--route").c_str())]; NSError* error = nil;
            id json = bytes ? [NSJSONSerialization JSONObjectWithData:bytes options:0 error:&error] : nil;
            require([json isKindOfClass:[NSDictionary class]] && [json[@"steps"] isKindOfClass:[NSArray class]], "Invalid route JSON");
            for (id step in json[@"steps"]) {
                require([step isKindOfClass:[NSDictionary class]] && [step[@"frames"] isKindOfClass:[NSNumber class]] &&
                        (!step[@"buttons"] || [step[@"buttons"] isKindOfClass:[NSNumber class]]), "Invalid route step");
                int64_t n = [step[@"frames"] longLongValue], b = [step[@"buttons"] longLongValue];
                require(n > 0 && n <= 1000000 && route.size() + n <= 1000000 && b >= 0 && !(uint64_t(b) & ~uint64_t(validMask)), "Invalid route frames/button mask");
                route.insert(route.end(), size_t(n), uint32_t(b));
            }
        }
        size_t frames = args.count("--frames") ? std::stoull(args.at("--frames")) : route.empty() ? 1200 : route.size();
        require(frames > 0 && frames <= 1000000 && (route.empty() || frames <= route.size()), "Frame count outside route/bounds");
        route.resize(std::max(route.size(), frames), 0);
        unsigned captureEvery = args.count("--capture-every") ? std::stoul(args.at("--capture-every")) : 0;
        options = {{"reicast_threaded_rendering", "disabled"}, {"reicast_internal_resolution", "640x480"},
                   {"reicast_region", "USA"}, {"reicast_language", "English"}, {"reicast_hle_bios", "disabled"},
                   {"reicast_enable_dsp", "enabled"}, {"reicast_force_freeplay", "disabled"},
                   {"reicast_detect_vsync_swap_interval", "disabled"}, {"reicast_auto_skip_frame", "disabled"},
                   {"reicast_frame_skipping", "disabled"}, {"reicast_widescreen_cheats", "disabled"},
                   {"reicast_widescreen_hack", "disabled"}, {"reicast_upnp", "disabled"}, {"reicast_dcnet", "disabled"},
                   {"reicast_per_content_vmus", "All VMUs"}, {"reicast_enable_rttb", rttReadback}};
        library = dlopen(fs::absolute(args.at("--core")).c_str(), RTLD_NOW | RTLD_LOCAL);
        require(library != nullptr, std::string("Cannot open core: ") + (library ? "" : dlerror()));
        auto setEnvironment = symbol<void(*)(retro_environment_t)>(library, "retro_set_environment");
        auto init = symbol<void(*)()>(library, "retro_init"); deinit = symbol<void(*)()>(library, "retro_deinit");
        auto load = symbol<bool(*)(const retro_game_info*)>(library, "retro_load_game");
        unload = symbol<void(*)()>(library, "retro_unload_game");
        auto run = symbol<void(*)()>(library, "retro_run");
        auto ticks = reinterpret_cast<uint64_t(*)()>(dlsym(library, "vt_fixed_ticks"));
        auto avInfo = symbol<void(*)(retro_system_av_info*)>(library, "retro_get_system_av_info");
        auto coreInfo = symbol<void(*)(retro_system_info*)>(library, "retro_get_system_info");
        require(symbol<unsigned(*)()>(library, "retro_api_version")() == RETRO_API_VERSION, "Unsupported libretro API");
        setEnvironment(environment);
        symbol<void(*)(retro_video_refresh_t)>(library, "retro_set_video_refresh")(video);
        symbol<void(*)(retro_audio_sample_t)>(library, "retro_set_audio_sample")(audioOne);
        symbol<void(*)(retro_audio_sample_batch_t)>(library, "retro_set_audio_sample_batch")(audio);
        symbol<void(*)(retro_input_poll_t)>(library, "retro_set_input_poll")(pollInput);
        symbol<void(*)(retro_input_state_t)>(library, "retro_set_input_state")(input);
        init(); initialized = true;
        auto port = symbol<void(*)(unsigned,unsigned)>(library, "retro_set_controller_port_device");
        port(0, RETRO_DEVICE_JOYPAD); port(1, RETRO_DEVICE_JOYPAD); port(2, RETRO_DEVICE_NONE); port(3, RETRO_DEVICE_NONE);
        std::string content = fs::absolute(args.at("--content")).string();
        retro_game_info game{content.c_str(), nullptr, 0, nullptr};
        require(load(&game), "Core rejected content; inspect stderr for the original engine diagnostic"); loaded = true;
        require(hardware.context_reset != nullptr && hardware.context_destroy != nullptr, "Core did not establish GL lifecycle callbacks");
        avInfo(&av); hardware.context_reset(); resetContext = true;
        retro_system_info info{}; coreInfo(&info);
        std::ofstream records(output / "frames.jsonl"); require(records.good(), "Cannot open frame records");
        CC_SHA256_Init(&audioStream); auto began = std::chrono::steady_clock::now();
        for (size_t i = 0; i < frames; ++i) {
            buttons = route[i]; pcm.clear(); gotFrame = false; duplicateFrame = false; videoCalls = 0;
            uint64_t beforeTicks = ticks ? ticks() : 0;
            run();
            require(!shutdownRequested && failure.empty(), failure.empty() ? "Core requested shutdown" : failure);
            require(videoCalls == 1, "Expected exactly one frontend video callback per retro_run");
            if (!pcm.empty()) CC_SHA256_Update(&audioStream, pcm.data(), (CC_LONG)(pcm.size() * sizeof(int16_t)));
            totalSamples += pcm.size() / 2;
            records << "{\"frame\":" << i + 1 << ",\"buttons\":" << buttons << ",\"width\":" << pictureWidth << ",\"height\":" << pictureHeight
                    << ",\"duplicate\":" << (duplicateFrame ? "true" : "false") << ",\"rgbaSHA256\":\"" << digest(picture.data(), picture.size())
                    << "\",\"audioFrames\":" << pcm.size() / 2 << ",\"pcmSHA256\":\"" << digest(pcm.data(), pcm.size() * 2) << "\"";
            if (ticks) records << ",\"sh4Ticks\":" << ticks() << ",\"stepTicks\":" << ticks() - beforeTicks;
            records << "}\n";
            if (captureEvery && (i + 1) % captureEvery == 0) savePNG(output / ("frame-" + std::to_string(i + 1) + ".png"));
            if ((i + 1) % 600 == 0) fprintf(stderr, "[harness] %zu/%zu frames\n", i + 1, frames);
        }
        records.close(); require(records.good(), "Frame record write failed");
        double elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - began).count();
        unsigned char stream[32]; CC_SHA256_Final(stream, &audioStream); char streamHex[65];
        for (unsigned i = 0; i < 32; ++i) snprintf(streamHex + i * 2, 3, "%02x", stream[i]);
        savePNG(output / "final.png");
        std::ofstream raw(output / "final.rgba", std::ios::binary); raw.write((const char*)picture.data(), picture.size()); raw.close();
        require(raw.good(), "Final RGBA write failed");
        NSMutableDictionary* optionJSON = [NSMutableDictionary dictionary];
        for (auto& [key, value] : options) optionJSON[@(key.c_str())] = @(value.c_str());
        NSMutableArray* unknown = [NSMutableArray array]; for (auto c : unknownEnvironment) [unknown addObject:@(c)];
        NSMutableDictionary* glErrors = [NSMutableDictionary dictionary];
        for (auto& [error, count] : coreGLErrors) glErrors[[NSString stringWithFormat:@"0x%x", error]] = @(count);
        writeJSON(output / "report.json", @{@"referenceOnly":@YES, @"frames":@(frames), @"stepUnit":@"libretro presentation/50-ms timeout boundary, not hardware vblank", @"width":@(pictureWidth), @"height":@(pictureHeight),
            @"coreName":@(info.library_name), @"coreVersion":@(info.library_version), @"fps":@(av.timing.fps), @"sampleRate":@(av.timing.sample_rate),
            @"coreSHA256":@(fileDigest(args.at("--core")).c_str()), @"contentSHA256":@(fileDigest(args.at("--content")).c_str()),
            @"harnessSHA256":@(fileDigest(argv[0]).c_str()), @"routeSHA256":args.count("--route") ? @(fileDigest(args.at("--route")).c_str()) : (id)[NSNull null],
            @"aspectRatio":@(av.geometry.aspect_ratio), @"elapsedSeconds":@(elapsed), @"measuredFramesPerSecond":@(frames / elapsed),
            @"stereoSampleFrames":@(totalSamples), @"rgbaSHA256":@(digest(picture.data(), picture.size()).c_str()), @"pcmStreamSHA256":@(streamHex),
            @"glVendor":@((const char*)glGetString(GL_VENDOR)), @"glRenderer":@((const char*)glGetString(GL_RENDERER)),
            @"glVersion":@((const char*)glGetString(GL_VERSION)), @"bottomLeftOrigin":@(hardware.bottom_left_origin),
            @"schedulerTicksAvailable":@(ticks != nullptr), @"coreOptions":optionJSON, @"upstreamPendingGLErrors":glErrors, @"unsupportedEnvironmentCommands":unknown});
        hardware.context_destroy(); resetContext = false; unload(); loaded = false; deinit(); initialized = false;
        destroyGL(); dlclose(library); library = nullptr;
        printf("Reference replay completed: %zu frames, %.2f frames/s.\n", frames, frames / elapsed); return 0;
    } catch (const std::exception& e) {
        fprintf(stderr, "REFERENCE HARNESS ERROR: %s\n", e.what());
        if (resetContext) hardware.context_destroy();
        if (loaded && unload) unload(); if (initialized && deinit) deinit();
        destroyGL(); if (library) dlclose(library); return 1;
    }
} }
