// SPDX-License-Identifier: GPL-2.0-only
// Platform adapter for the fixed Virtua Tennis engine; no guest CPU interpreter.
#include "virtua_tennis.h"
#include "vt_fixed_fault.h"
#include "media_identity.h"
#import <Foundation/Foundation.h>
#import <OpenGL/OpenGL.h>
#import <OpenGL/gl3.h>
#include <CommonCrypto/CommonDigest.h>
#include <libretro.h>
#include <dlfcn.h>
#include <algorithm>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>
#ifndef VT_FIXED_NATIVE
#error The production bridge requires the verified fixed engine target
#endif
extern "C" uint32_t vt_fixed_engine_marker(void);
namespace fs = std::filesystem;
struct vt_context {
    std::thread::id owner;
    std::string assets, saves, error;
    uint32_t fault = 0;
    uint64_t steps = 0, tickOrigin = 0;
    bool initialized = false, loaded = false, resetContext = false;
};
namespace {
std::recursive_mutex gate;
vt_context* active = nullptr;
std::string createError;
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
    // Offload driver command processing; guest CPUs, renderer submission and
    // synchronous frame readback still belong to this context's owner thread.
    // Toggle once during initialization, never while a frame is being drawn.
    const CGLError workerStatus = CGLEnable(context, kCGLCEMPEngine);
    GLint workerEnabled = GL_FALSE;
    const bool driverWorker = workerStatus == kCGLNoError
        && CGLIsEnabled(context, kCGLCEMPEngine, &workerEnabled) == kCGLNoError
        && workerEnabled == GL_TRUE;
    fprintf(stderr, "[graphics] OpenGL driver worker %s\n",
            driverWorker ? "enabled" : "not confirmed");
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

struct ContextScope {
    CGLContextObj previous = CGLGetCurrentContext();
    ContextScope() { if (context) CGLSetCurrentContext(context); }
    ~ContextScope() { CGLSetCurrentContext(previous); }
};
static bool live(const vt_context* c) { return c && c == active; }
static void check(vt_context* c) {
    require(live(c), "The game session is closed or invalid");
    require(c->owner == std::this_thread::get_id(), "Virtua Tennis engine calls must stay on the creating thread");
}
static bool isInside(const fs::path& child, const fs::path& parent) {
    auto p = parent.begin(), q = child.begin();
    for (; p != parent.end(); ++p, ++q) if (q == child.end() || *p != *q) return false;
    return true;
}
static void verifyMedia(const fs::path& assets) {
    require(fs::is_directory(assets), "Virtua Tennis media directory is missing");
    for (size_t i = 0; i < vt_media_count; ++i) {
        const auto& entry = vt_media[i]; fs::path path = assets / entry.path;
        require(fs::is_regular_file(path) && !fs::is_symlink(path), std::string("Missing or unsafe media: ") + entry.path);
        require(fs::file_size(path) == entry.bytes, std::string("Incorrect media length: ") + entry.path);
        require(fileDigest(path) == entry.sha256, std::string("Incorrect media identity: ") + entry.path);
    }
}
static void stop(vt_context* c) {
    if (c->resetContext) { hardware.context_destroy(); c->resetContext = false; }
    if (c->loaded) { retro_unload_game(); c->loaded = false; }
    if (c->initialized) { retro_deinit(); c->initialized = false; }
    destroyGL();
}
static void start(vt_context* c) {
    require(vt_fixed_engine_marker() == 0x56544658, "This app requires the verified fixed Virtua Tennis engine");
    verifyMedia(c->assets);
    fs::path saves = fs::weakly_canonical(c->saves), assets = fs::weakly_canonical(c->assets);
    require(!isInside(saves, assets), "The writable data directory must be outside the media directory");
    require(isInside(fs::weakly_canonical(saves / "system/dc"), saves), "The system cache must remain inside the writable data directory");
    fs::create_directories(saves / "system/dc/data");
    // Upstream's system directory can receive VMU/cache writes, so give it a
    // private writable copy of the already verified BIOS, never bundle storage.
    fs::path bios = saves / "system/dc/naomi.zip";
    require(!fs::is_symlink(bios), "Unsafe BIOS cache path");
    fs::copy_file(assets / "system/dc/naomi.zip", bios, fs::copy_options::overwrite_existing);
    systemPath = (saves / "system").string(); savePath = saves.string();
    options = {{"reicast_threaded_rendering", "disabled"}, {"reicast_internal_resolution", "640x480"},
               {"reicast_region", "USA"}, {"reicast_language", "English"}, {"reicast_hle_bios", "disabled"},
               {"reicast_enable_dsp", "enabled"}, {"reicast_force_freeplay", "disabled"},
               {"reicast_detect_vsync_swap_interval", "disabled"}, {"reicast_auto_skip_frame", "disabled"},
               {"reicast_frame_skipping", "disabled"}, {"reicast_widescreen_cheats", "disabled"},
               {"reicast_widescreen_hack", "disabled"}, {"reicast_upnp", "disabled"}, {"reicast_dcnet", "disabled"},
               {"reicast_per_content_vmus", "All VMUs"},
               // The intro samples rendered images through VRAM for its
               // monochrome effect. GPU-only RTT caching produces flat grey.
               {"reicast_enable_rttb", "enabled"}};
    hardware = {}; av = {}; failure.clear(); shutdownRequested = false;
    buttons = 0; pictureWidth = pictureHeight = videoCalls = 0; picture.clear(); pcm.clear();
    coreGLErrors.clear(); unknownEnvironment.clear(); vt_fixed_clear_error();
    createGL();
    require(retro_api_version() == RETRO_API_VERSION, "Unexpected fixed engine frontend ABI");
    retro_set_environment(environment); retro_set_video_refresh(video);
    retro_set_audio_sample(audioOne); retro_set_audio_sample_batch(audio);
    retro_set_input_poll(pollInput); retro_set_input_state(input);
    retro_init(); c->initialized = true;
    retro_set_controller_port_device(0, RETRO_DEVICE_JOYPAD); retro_set_controller_port_device(1, RETRO_DEVICE_JOYPAD);
    retro_set_controller_port_device(2, RETRO_DEVICE_NONE); retro_set_controller_port_device(3, RETRO_DEVICE_NONE);
    std::string content = (assets / "vtennis.zip").string();
    retro_game_info game{content.c_str(), nullptr, 0, nullptr};
    require(retro_load_game(&game), "Virtua Tennis could not load its verified cartridge and USA BIOS"); c->loaded = true;
    require(hardware.context_reset && hardware.context_destroy, "Fixed engine did not provide its renderer lifecycle");
    retro_get_system_av_info(&av); hardware.context_reset(); c->resetContext = true;
    require(av.timing.sample_rate == 44100 && av.timing.fps > 1 && av.timing.fps < 240, "Invalid NAOMI video/audio timing");
    c->tickOrigin = vt_fixed_ticks(); c->steps = 0; c->error.clear(); c->fault = 0;
}
static int fail(vt_context* c, const std::exception& e) {
    if (live(c)) { c->error = e.what(); c->fault = 1; }
    else createError = e.what();
    return 0;
}
} // namespace

extern "C" vt_context* vt_create(const char* assets, const char* saves) { @autoreleasepool {
    std::lock_guard<std::recursive_mutex> lock(gate); ContextScope current;
    if (active) { createError = "Only one Virtua Tennis game session may run in a process"; return nullptr; }
    auto c = new vt_context;
    try {
        require(assets && *assets && saves && *saves, "Explicit media and writable save directories are required");
        c->owner = std::this_thread::get_id(); c->assets = fs::absolute(assets).string(); c->saves = fs::absolute(saves).string();
        active = c; start(c); createError.clear(); return c;
    } catch (const std::exception& e) {
        createError = e.what(); stop(c); active = nullptr; delete c; return nullptr;
    }
} }
extern "C" void vt_destroy(vt_context* c) { @autoreleasepool {
    std::lock_guard<std::recursive_mutex> lock(gate); if (!live(c)) return; ContextScope current;
    try { check(c); stop(c); active = nullptr; delete c; }
    catch (const std::exception& e) { fail(c, e); }
} }
extern "C" int vt_reset(vt_context* c) { @autoreleasepool {
    std::lock_guard<std::recursive_mutex> lock(gate); ContextScope current;
    try { check(c); stop(c); start(c); return 1; }
    catch (const std::exception& e) { return fail(c, e); }
} }
extern "C" int vt_step(vt_context* c, uint32_t mask) { @autoreleasepool {
    std::lock_guard<std::recursive_mutex> lock(gate); ContextScope current;
    try {
        check(c); require(!c->fault, c->error); require(c->loaded, "The game did not initialize");
        require(!(mask & ~validMask), "Unsupported Virtua Tennis button bits");
        buttons = mask; pcm.clear(); videoCalls = 0; gotFrame = false; duplicateFrame = false;
        uint64_t previous = vt_fixed_ticks(); retro_run();
        const char* cpuError = vt_fixed_error();
        require(!cpuError || !*cpuError, cpuError ? cpuError : "Fixed CPU failure");
        require(!shutdownRequested && failure.empty(), failure.empty() ? "The game engine requested shutdown" : failure);
        require(videoCalls == 1, "The game returned an invalid presentation boundary");
        require(vt_fixed_ticks() > previous && vt_fixed_ticks() - previous < 200000000ULL,
                "The fixed engine returned invalid emulated time");
        ++c->steps; return 1;
    } catch (const std::exception& e) { return fail(c, e); }
} }
extern "C" const char* vt_error(const vt_context* c) {
    std::lock_guard<std::recursive_mutex> lock(gate); return live(c) ? c->error.c_str() : createError.c_str();
}
extern "C" uint32_t vt_fault_code(const vt_context* c) {
    std::lock_guard<std::recursive_mutex> lock(gate); return live(c) ? c->fault : 1;
}
extern "C" uint64_t vt_frame_number(const vt_context* c) {
    std::lock_guard<std::recursive_mutex> lock(gate); return live(c) ? c->steps : 0;
}
extern "C" double vt_emulated_seconds(const vt_context* c) {
    std::lock_guard<std::recursive_mutex> lock(gate); return live(c) ? (vt_fixed_ticks() - c->tickOrigin) / 200000000.0 : 0;
}
extern "C" double vt_frame_rate(const vt_context* c) {
    std::lock_guard<std::recursive_mutex> lock(gate); return live(c) ? av.timing.fps : 0;
}
extern "C" int vt_width(const vt_context* c) {
    std::lock_guard<std::recursive_mutex> lock(gate); return live(c) ? (pictureWidth ? pictureWidth : av.geometry.base_width) : 0;
}
extern "C" int vt_height(const vt_context* c) {
    std::lock_guard<std::recursive_mutex> lock(gate); return live(c) ? (pictureHeight ? pictureHeight : av.geometry.base_height) : 0;
}
extern "C" double vt_aspect_ratio(const vt_context* c) {
    std::lock_guard<std::recursive_mutex> lock(gate); return live(c) ? av.geometry.aspect_ratio : 0;
}
extern "C" const uint8_t* vt_pixels(const vt_context* c) {
    std::lock_guard<std::recursive_mutex> lock(gate); return live(c) && !picture.empty() ? picture.data() : nullptr;
}
extern "C" const int16_t* vt_audio(const vt_context* c) {
    std::lock_guard<std::recursive_mutex> lock(gate); return live(c) && !pcm.empty() ? pcm.data() : nullptr;
}
extern "C" uint32_t vt_audio_count(const vt_context* c) {
    std::lock_guard<std::recursive_mutex> lock(gate); return live(c) ? uint32_t(pcm.size() / 2) : 0;
}
extern "C" uint32_t vt_audio_sample_rate(const vt_context* c) {
    std::lock_guard<std::recursive_mutex> lock(gate); return live(c) ? uint32_t(av.timing.sample_rate) : 0;
}
