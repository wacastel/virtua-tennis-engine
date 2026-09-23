import Foundation
import CryptoKit
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

@_silgen_name("vt_create") private func nativeCreate(_ assets: UnsafePointer<CChar>, _ saves: UnsafePointer<CChar>) -> UnsafeMutableRawPointer?
@_silgen_name("vt_destroy") private func nativeDestroy(_ context: UnsafeMutableRawPointer)
@_silgen_name("vt_reset") private func nativeReset(_ context: UnsafeMutableRawPointer) -> Int32
@_silgen_name("vt_error") private func nativeError(_ context: UnsafeMutableRawPointer?) -> UnsafePointer<CChar>?
@_silgen_name("vt_fault_code") private func nativeFaultCode(_ context: UnsafeMutableRawPointer) -> UInt32
@_silgen_name("vt_step") private func nativeStep(_ context: UnsafeMutableRawPointer, _ buttons: UInt32) -> Int32
@_silgen_name("vt_pixels") private func nativePixels(_ context: UnsafeMutableRawPointer) -> UnsafePointer<UInt8>?
@_silgen_name("vt_audio") private func nativeAudio(_ context: UnsafeMutableRawPointer) -> UnsafePointer<Int16>?
@_silgen_name("vt_audio_count") private func nativeAudioCount(_ context: UnsafeMutableRawPointer) -> Int32
@_silgen_name("vt_width") private func nativeWidth(_ context: UnsafeMutableRawPointer) -> Int32
@_silgen_name("vt_height") private func nativeHeight(_ context: UnsafeMutableRawPointer) -> Int32
@_silgen_name("vt_frame_rate") private func nativeFrameRate(_ context: UnsafeMutableRawPointer) -> Double
@_silgen_name("vt_audio_sample_rate") private func nativeSampleRate(_ context: UnsafeMutableRawPointer) -> Int32
@_silgen_name("vt_frame_number") private func nativeFrameNumber(_ context: UnsafeMutableRawPointer) -> UInt64
@_silgen_name("vt_emulated_seconds") private func nativeEmulatedSeconds(_ context: UnsafeMutableRawPointer) -> Double

enum VTPreferences {
    static let domain = "local.william.virtuatennis"
    static let defaults: UserDefaults = Bundle.main.bundleIdentifier == domain ? .standard : UserDefaults(suiteName: domain)!
    static let mutedKey = "muted"
}

final class VTGame {
    struct Frame {
        let rgba: Data
        let width: Int, height: Int
        var image: CGImage? {
            guard rgba.count == width * height * 4, let provider = CGDataProvider(data: rgba as CFData) else { return nil }
            return CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 32,
                           bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
                           bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue), provider: provider,
                           decode: nil, shouldInterpolate: false, intent: .defaultIntent)
        }
        func writePNG(to url: URL) throws {
            guard let image, let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else {
                throw VirtuaTennisError.message("Cannot create capture at \(url.path).")
            }
            CGImageDestinationAddImage(destination, image, nil)
            guard CGImageDestinationFinalize(destination) else { throw VirtuaTennisError.message("Could not save the frame capture.") }
        }
    }
    private let context: UnsafeMutableRawPointer
    private let media: VTMedia
    private var closed = false
    let sampleRate: Int
    var width: Int { closed ? 640 : Int(nativeWidth(context)) }
    var height: Int { closed ? 480 : Int(nativeHeight(context)) }
    var framesPerSecond: Double { closed ? 0 : nativeFrameRate(context) }
    private(set) var emulatedSeconds: Double = 0
    private(set) var frameCount = 0
    private(set) var sampleFrames = 0
    private(set) var latestFrame: Frame?

    init() throws {
        let media = try VTMedia.resolve()
        let pointer = media.assets.path.withCString { assets in media.saves.path.withCString { nativeCreate(assets, $0) } }
        guard let pointer else {
            media.removeTemporarySaves()
            throw VirtuaTennisError.message(nativeError(nil).map { String(cString: $0) } ?? "The Virtua Tennis native engine could not initialize.")
        }
        let width = Int(nativeWidth(pointer)), height = Int(nativeHeight(pointer))
        let fps = nativeFrameRate(pointer), rate = Int(nativeSampleRate(pointer))
        guard width == 640, height == 480, fps.isFinite, (1...240).contains(fps), rate == 44100 else {
            nativeDestroy(pointer); media.removeTemporarySaves()
            throw VirtuaTennisError.message("The native engine returned an unsupported NAOMI video or audio format.")
        }
        self.context = pointer; self.media = media; self.sampleRate = rate
    }
    deinit { close() }
    func close() {
        guard !closed else { return }
        closed = true; nativeDestroy(context); media.removeTemporarySaves()
    }
    private func requireOpen() throws {
        guard !closed else { throw VirtuaTennisError.message("The game session has closed.") }
    }
    private var failure: VirtuaTennisError {
        let text = nativeError(context).map { String(cString: $0) } ?? "The native engine stopped."
        return .message("\(text) (fault \(nativeFaultCode(context)))")
    }
    func reset() throws {
        try requireOpen()
        guard nativeReset(context) == 1 else { throw failure }
        frameCount = 0; sampleFrames = 0; latestFrame = nil; emulatedSeconds = 0
    }
    func advance(_ input: VTInput) throws -> [Int16] {
        try requireOpen(); try input.validate()
        guard nativeStep(context, input.buttons) == 1 else { throw failure }
        guard nativeFaultCode(context) == 0 else { throw failure }
        guard nativeFrameNumber(context) == UInt64(frameCount + 1) else {
            throw VirtuaTennisError.message("The engine did not return the expected completed video frame.")
        }
        let time = nativeEmulatedSeconds(context)
        guard time.isFinite, time > emulatedSeconds, time - emulatedSeconds < 1 else {
            throw VirtuaTennisError.message("The engine returned invalid emulated time.")
        }
        emulatedSeconds = time
        guard (1...2048).contains(width), (1...2048).contains(height) else {
            throw VirtuaTennisError.message("The engine returned invalid picture dimensions.")
        }
        // BIOS calls can advance without presenting. Keep the previous picture.
        if let pixels = nativePixels(context) {
            latestFrame = Frame(rgba: Data(bytes: pixels, count: width * height * 4), width: width, height: height)
        }
        let count = Int(nativeAudioCount(context))
        guard (0...88200).contains(count) else { throw VirtuaTennisError.message("Invalid native stereo-frame count.") }
        var samples = [Int16]()
        if count > 0 {
            guard let pointer = nativeAudio(context) else { throw VirtuaTennisError.message("The engine returned no PCM buffer.") }
            samples = Array(UnsafeBufferPointer(start: pointer, count: count * 2))
        }
        frameCount += 1; sampleFrames += count
        return samples
    }
    func diagnostics() -> [String: Any] {
        ["frame": frameCount, "emulatedSeconds": emulatedSeconds, "closed": closed,
         "faultCode": closed ? 0 : nativeFaultCode(context), "framesPerSecond": framesPerSecond,
         "sampleRate": sampleRate, "width": width, "height": height]
    }
}

func runVTReplay(frames: Int, replay: VTReplay?, capture: URL?, trace: URL? = nil) throws -> [String: Any] {
    let game = try VTGame(); defer { game.close() }
    var pictures = SHA256(), audio = SHA256(), framedAudio = SHA256(), nonzeroSamples = 0
    let records: FileHandle?
    if let trace {
        try Data().write(to: trace, options: .atomic)
        records = try FileHandle(forWritingTo: trace)
    } else { records = nil }
    defer { try? records?.close() }
    func digest(_ value: SHA256.Digest) -> String { value.map { String(format: "%02x", $0) }.joined() }
    let started = Date()
    for frame in 0..<frames {
        let samples = try game.advance(replay?.input(frame: frame) ?? VTInput())
        if let picture = game.latestFrame { pictures.update(data: picture.rgba) }
        let pcm = samples.withUnsafeBytes { Data($0) }
        audio.update(data: pcm)
        var sampleCount = UInt32(samples.count / 2).littleEndian
        withUnsafeBytes(of: &sampleCount) { framedAudio.update(data: Data($0)) }
        framedAudio.update(data: pcm)
        if let records {
            let rgba = game.latestFrame?.rgba ?? Data()
            let row: [String: Any] = ["frame": frame + 1, "audioFrames": samples.count / 2,
                "rgba": digest(SHA256.hash(data: rgba)), "pcm": digest(SHA256.hash(data: pcm)),
                "emulatedSeconds": game.emulatedSeconds]
            var data = try JSONSerialization.data(withJSONObject: row, options: [.sortedKeys])
            data.append(10); try records.write(contentsOf: data)
        }
        nonzeroSamples += samples.reduce(0) { $0 + ($1 == 0 ? 0 : 1) }
    }
    let finalRGBA = game.latestFrame?.rgba ?? Data()
    if let capture {
        guard let picture = game.latestFrame else { throw VirtuaTennisError.message("The BIOS has not presented a picture yet.") }
        try picture.writePNG(to: capture)
    }
    return ["game": "Virtua Tennis", "frames": frames, "framesPerSecond": game.framesPerSecond,
            "width": game.width, "height": game.height, "pixelFormat": "RGBA8888", "audioSampleRate": game.sampleRate,
            "sampleFrames": game.sampleFrames, "nonzeroSamples": nonzeroSamples,
            "pictureSHA256": digest(pictures.finalize()), "audioSHA256": digest(audio.finalize()),
            "audioFrameSequenceSHA256": digest(framedAudio.finalize()),
            "finalPictureSHA256": digest(SHA256.hash(data: finalRGBA)), "finalState": game.diagnostics(),
            "elapsedSeconds": Date().timeIntervalSince(started), "emulatedSeconds": game.emulatedSeconds,
            "stepUnit": "presentation or no-draw timeout boundary, not hardware vblank"]
}
