// SPDX-License-Identifier: GPL-2.0-only
// Developer-only synthetic comparison of the actual production texture helper.
// No engine, game media, audio device, application preferences or normal saves.
import AppKit
import SpriteKit
import CryptoKit
import ImageIO
import UniformTypeIdentifiers

struct Failure: Error, CustomStringConvertible {
    let description: String
    init(_ text: String) { description = text }
}

struct Frame {
    let rgba: Data
    let width: Int
    let height: Int
    var size: CGSize { CGSize(width: width, height: height) }

    // Matches Sources/Mac/NativeGame.swift's production CGImage construction.
    func originalImage() throws -> CGImage {
        guard rgba.count == width * height * 4,
              let provider = CGDataProvider(data: rgba as CFData),
              let image = CGImage(width: width, height: height, bitsPerComponent: 8,
                  bitsPerPixel: 32, bytesPerRow: width * 4,
                  space: CGColorSpaceCreateDeviceRGB(),
                  bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.noneSkipLast.rawValue),
                  provider: provider, decode: nil, shouldInterpolate: false,
                  intent: .defaultIntent) else { throw Failure("Cannot construct original CGImage") }
        return image
    }


}

func originalTexture(_ frame: Frame) throws -> SKTexture {
    let texture = SKTexture(cgImage: try frame.originalImage())
    texture.filteringMode = .nearest
    return texture
}

func productionTexture(_ frame: Frame) throws -> SKTexture {
    guard let texture = makeVTFrameTexture(rgba: frame.rgba, width: frame.width, height: frame.height)
    else { throw Failure("Production helper rejected valid synthetic data") }
    return texture
}

// Negative control proves that comparisons detect the wrong row orientation.
func reversedTexture(_ frame: Frame) -> SKTexture {
    var opaque = frame.rgba
    opaque.withUnsafeMutableBytes { (raw: UnsafeMutableRawBufferPointer) in
        let bytes = raw.bindMemory(to: UInt8.self)
        for i in stride(from: 3, to: bytes.count, by: 4) { bytes[i] = 255 }
    }
    let texture = SKTexture(data: opaque, size: frame.size, flipped: false)
    texture.filteringMode = .nearest
    return texture
}

func sha(_ data: Data) -> String {
    SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
}

func pattern(width: Int, height: Int, seed: Int) -> Frame {
    var bytes = [UInt8](repeating: 0, count: width * height * 4)
    for y in 0..<height {
        for x in 0..<width {
            let i = (y * width + x) * 4
            let band = y * 6 / height
            switch band {
            case 0: // Distinct red top row, horizontal ramp and row markers.
                bytes[i] = 255; bytes[i + 1] = UInt8(x * 255 / max(1, width - 1)); bytes[i + 2] = UInt8((y * 17 + seed) & 255)
            case 1: // Nonlinear gray ramp to detect color conversion differences.
                let value = UInt8(((x * x + 11 * y + seed * 7) / 3) & 255)
                bytes[i] = value; bytes[i + 1] = value; bytes[i + 2] = value
            case 2: // Cyan/green and a deliberately asymmetric boundary.
                bytes[i] = UInt8((x + seed * 13) & 255); bytes[i + 1] = 255; bytes[i + 2] = x < width / 3 ? 0 : 255
            case 3: // Near-black and midtone channel-independent values.
                bytes[i] = UInt8((x * 3 + y * 2 + seed) & 255)
                bytes[i + 1] = UInt8((x * 5 + y * 7 + seed * 3) & 255)
                bytes[i + 2] = UInt8((x * 11 + y * 13 + seed * 5) & 255)
            case 4: // Opaque white/black checkerboard.
                let value: UInt8 = (x / 3 + y / 2 + seed) & 1 == 0 ? 0 : 255
                bytes[i] = value; bytes[i + 1] = value; bytes[i + 2] = value
            default: // Distinct blue bottom row.
                bytes[i] = UInt8((y * 31 + seed) & 255); bytes[i + 1] = UInt8(x * 255 / max(1, width - 1)); bytes[i + 2] = 255
            }
            let alpha: [UInt8] = [0, 1, 17, 64, 127, 128, 254, 255]
            bytes[i + 3] = alpha[(x + 3 * y + seed) % alpha.count]
        }
    }
    // Different corner identities detect row reversal and channel swaps.
    for (x, y, rgb) in [(0, 0, [255, 0, 0]), (width - 1, 0, [0, 255, 0]),
                         (0, height - 1, [0, 0, 255]), (width - 1, height - 1, [255, 255, 0])] {
        let i = (y * width + x) * 4
        for c in 0..<3 { bytes[i + c] = UInt8(rgb[c]) }
    }
    return Frame(rgba: Data(bytes), width: width, height: height)
}

// Normalize BOTH paths through the identical explicitly named color space and
// byte order. No comparison relies on a provider's private row/pixel format.
func normalized(_ image: CGImage) throws -> Data {
    let width = image.width, height = image.height
    var result = Data(count: width * height * 4)
    try result.withUnsafeMutableBytes { (raw: UnsafeMutableRawBufferPointer) in
        guard let context = CGContext(data: raw.baseAddress, width: width, height: height,
            bitsPerComponent: 8, bytesPerRow: width * 4,
            space: CGColorSpace(name: CGColorSpace.sRGB)!,
            bitmapInfo: CGBitmapInfo.byteOrder32Big.rawValue | CGImageAlphaInfo.premultipliedLast.rawValue)
        else { throw Failure("Cannot construct normalization context") }
        context.interpolationQuality = .none
        context.setBlendMode(.copy)
        context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
    }
    return result
}

func difference(_ reference: Data, _ candidate: Data) -> [String: Any] {
    guard reference.count == candidate.count else {
        return ["exact": false, "referenceByteCount": reference.count, "candidateByteCount": candidate.count]
    }
    let a = [UInt8](reference), b = [UInt8](candidate)
    var differentBytes = 0, differentPixels = 0, maximumDelta = 0, totalDelta = 0
    var firstDifferences = [[String: Int]]()
    for pixel in 0..<(a.count / 4) {
        var changed = false
        for channel in 0..<4 {
            let index = pixel * 4 + channel, delta = abs(Int(a[index]) - Int(b[index]))
            if delta != 0 {
                differentBytes += 1; changed = true
                maximumDelta = max(maximumDelta, delta); totalDelta += delta
                if firstDifferences.count < 12 { firstDifferences.append(["pixel": pixel, "channel": channel, "reference": Int(a[index]), "candidate": Int(b[index])]) }
            }
        }
        if changed { differentPixels += 1 }
    }
    return ["exact": differentBytes == 0, "differentBytes": differentBytes,
        "differentPixels": differentPixels, "maximumChannelDelta": maximumDelta,
        "meanAbsoluteChannelDelta": Double(totalDelta) / Double(max(1, a.count)),
        "firstDifferences": firstDifferences, "referenceSHA256": sha(reference), "candidateSHA256": sha(candidate)]
}

func coverage(_ data: Data) -> [String: Any] {
    let bytes = [UInt8](data)
    var colors = Set<UInt32>(), clearPixels = 0, nonOpaquePixels = 0
    for i in stride(from: 0, to: bytes.count, by: 4) {
        colors.insert(UInt32(bytes[i]) << 16 | UInt32(bytes[i + 1]) << 8 | UInt32(bytes[i + 2]))
        if bytes[i] == 255 && bytes[i + 1] == 0 && bytes[i + 2] == 255 { clearPixels += 1 }
        if bytes[i + 3] != 255 { nonOpaquePixels += 1 }
    }
    let fraction = Double(clearPixels) / Double(max(1, bytes.count / 4))
    return ["distinctRGBColors": colors.count, "magentaBackgroundFraction": fraction,
        "nonOpaquePixels": nonOpaquePixels,
        "passed": colors.count > 16 && fraction < 0.01 && nonOpaquePixels == 0]
}

func writePNG(_ image: CGImage, to path: URL) throws {
    guard let output = CGImageDestinationCreateWithURL(path as CFURL, UTType.png.identifier as CFString, 1, nil)
    else { throw Failure("Cannot create PNG") }
    CGImageDestinationAddImage(output, image, nil)
    guard CGImageDestinationFinalize(output) else { throw Failure("Cannot finish PNG") }
}

func renderedImage(_ texture: SKTexture, size: CGSize, view: SKView) throws -> CGImage {
    // One shared view, identical opaque background/node geometry for both paths.
    view.frame = CGRect(origin: .zero, size: size)
    let scene = SKScene(size: size)
    scene.scaleMode = .aspectFit; scene.backgroundColor = .magenta
    let sprite = SKSpriteNode(texture: texture, size: size)
    sprite.position = CGPoint(x: size.width / 2, y: size.height / 2)
    scene.addChild(sprite)
    view.presentScene(scene)
    guard let snapshot = view.texture(from: scene, crop: CGRect(origin: .zero, size: size)) else {
        throw Failure("SKView could not render the synthetic scene")
    }
    return snapshot.cgImage()
}

func samplesSummary(_ values: [Double]) -> [String: Any] {
    let sorted = values.sorted()
    guard !sorted.isEmpty else { return ["count": 0] }
    let total = sorted.reduce(0, +)
    return ["count": sorted.count, "totalSeconds": total,
        "meanMilliseconds": total / Double(sorted.count) * 1000,
        "medianMilliseconds": sorted[sorted.count / 2] * 1000,
        "p95Milliseconds": sorted[min(sorted.count - 1, Int(ceil(Double(sorted.count) * 0.95)) - 1)] * 1000,
        "maximumMilliseconds": sorted.last! * 1000]
}

@main struct TexturePresentationHarness {
    static func main() {
        do { try run() }
        catch { fputs("Texture presentation: \(error)\n", stderr); exit(1) }
    }

    static func run() throws {
        precondition(Thread.isMainThread)
        let arguments = Array(CommandLine.arguments.dropFirst())
        guard arguments.count == 1 || arguments.count == 3,
              arguments.count == 1 || arguments[1] == "--benchmark-iterations" else {
            throw Failure("Usage: texture-presentation-harness FRESH_OUTPUT_DIRECTORY [--benchmark-iterations 400]")
        }
        let iterations = arguments.count == 3 ? Int(arguments[2]) ?? -1 : 0
        guard (0...2000).contains(iterations) else { throw Failure("Iterations must be 0...2000") }
        let output = URL(fileURLWithPath: arguments[0], isDirectory: true)
        guard !FileManager.default.fileExists(atPath: output.path) else { throw Failure("Output already exists") }
        let executable = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
        let buildDirectory = executable.deletingLastPathComponent()
        let manifestData = try Data(contentsOf: buildDirectory.appendingPathComponent("manifest.json"))
        guard let manifest = try JSONSerialization.jsonObject(with: manifestData) as? [String: Any],
              let copiedSources = manifest["copiedSources"] as? [String: String],
              copiedSources.keys.sorted() == ["FramePresentation.swift", "TexturePresentationHarness.swift"],
              manifest["executableSHA256"] as? String == sha(try Data(contentsOf: executable)) else {
            throw Failure("Build manifest/executable identity mismatch")
        }
        func verifySources() throws {
            for (name, expected) in copiedSources {
                guard sha(try Data(contentsOf: buildDirectory.appendingPathComponent(name))) == expected else {
                    throw Failure("Copied production/helper source changed: " + name)
                }
            }
        }
        try verifySources()
        try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        _ = NSApplication.shared
        NSApp.setActivationPolicy(.prohibited)
        let view = SKView(frame: CGRect(x: 0, y: 0, width: 640, height: 480))
        view.isAsynchronous = false
        view.allowsTransparency = false
        view.preferredFramesPerSecond = 60
        var guardChecks = [[String: Any]]()
        for (width, height, count) in [(0, 1, 4), (1, 0, 4), (-1, 1, 4), (1, -1, 4),
                (2049, 1, 4), (1, 2049, 4), (Int.max, Int.max, 4),
                (1, 1, 0), (1, 1, 3), (1, 1, 5), (2, 2, 15), (2, 2, 17)] {
            let rejected = makeVTFrameTexture(rgba: Data(count: count), width: width, height: height) == nil
            guardChecks.append(["width": width, "height": height, "byteCount": count, "rejected": rejected])
        }
        var comparisons = [[String: Any]]()
        let cases = [("minimal", 1, 1, 0), ("odd-small", 37, 29, 0), ("ramp-small", 64, 48, 1), ("game-size", 640, 480, 3)]
        for (name, width, height, seed) in cases {
            try autoreleasepool {
                let frame = pattern(width: width, height: height, seed: seed)
                let sourceBefore = sha(frame.rgba)
                let original = try originalTexture(frame)
                let originalImage = original.cgImage()
                let originalBytes = try normalized(originalImage)
                let originalRender = try renderedImage(original, size: frame.size, view: view)
                let originalRenderedBytes = try normalized(originalRender)
                let textureCoverage = coverage(originalBytes)
                let renderCoverage = coverage(originalRenderedBytes)
                let candidate = try productionTexture(frame)
                let image = candidate.cgImage()
                let render = try renderedImage(candidate, size: frame.size, view: view)
                let textureComparison = difference(originalBytes, try normalized(image))
                let renderComparison = difference(originalRenderedBytes, try normalized(render))
                let sourceUnchanged = sourceBefore == sha(frame.rgba)
                let sameDimensions = image.width == originalImage.width && image.height == originalImage.height && render.width == originalRender.width && render.height == originalRender.height
                let nearest = candidate.filteringMode == .nearest
                let populated = name == "minimal" || (textureCoverage["passed"] as? Bool == true && renderCoverage["passed"] as? Bool == true)
                var negativeControlRejected = true
                if name != "minimal" {
                    let negative = reversedTexture(frame)
                    let negativeImage = difference(originalBytes, try normalized(negative.cgImage()))
                    let negativeRender = difference(originalRenderedBytes, try normalized(renderedImage(negative, size: frame.size, view: view)))
                    negativeControlRejected = negativeImage["exact"] as? Bool == false && negativeRender["exact"] as? Bool == false
                }
                let passed = textureComparison["exact"] as? Bool == true && renderComparison["exact"] as? Bool == true && sourceUnchanged && sameDimensions && nearest && populated && negativeControlRejected
                comparisons.append(["case": name, "width": width, "height": height,
                    "sourceSHA256": sourceBefore, "sourceRawAlphaUnchanged": sourceUnchanged,
                    "sameDimensions": sameDimensions, "nearestFiltering": nearest,
                    "originalTextureCoverage": textureCoverage, "originalRenderCoverage": renderCoverage,
                    "normalizedTextureComparison": textureComparison, "sameSKViewRenderComparison": renderComparison,
                    "wrongOrientationRejected": negativeControlRejected,
                    "wrongOrientationCheckApplicable": name != "minimal", "passed": passed])
                for (suffix, picture) in [("original-texture", originalImage), ("original-render", originalRender),
                                           ("production-texture", image), ("production-render", render)] {
                    try writePNG(picture, to: output.appendingPathComponent(name + "-" + suffix + ".png"))
                }
            }
        }
        view.presentScene(nil)
        let passed = guardChecks.allSatisfy { $0["rejected"] as? Bool == true } && comparisons.allSatisfy { $0["passed"] as? Bool == true }
        var report: [String: Any] = ["schemaVersion": 1, "passed": passed,
            "scope": "Actual production texture helper with synthetic patterns and same-view offscreen rendering. No native engine, media, input, clock, audio or on-screen game qualification.",
            "normalization": "Identical sRGB premultiplied-last RGBA8 big-endian CGContext for both images; all channel values must match exactly.",
            "invalidInputChecks": guardChecks, "comparisons": comparisons,
            "macOS": ProcessInfo.processInfo.operatingSystemVersionString,
            "executableSHA256": sha(try Data(contentsOf: executable)),
            "buildManifestSHA256": sha(manifestData), "copiedSources": copiedSources ]
        if passed && iterations > 0 {
            let frames = (0..<16).map { pattern(width: 640, height: 480, seed: $0) }
            let hashesBefore = frames.map { sha($0.rgba) }
            var benchmarkRows = [[String: Any]]()
            var checksum: Double = 0
            func create(_ frame: Frame, direct: Bool) throws -> SKTexture {
                try direct ? productionTexture(frame) : originalTexture(frame)
            }
            for direct in [false, true] {
                for i in 0..<32 { try autoreleasepool { checksum += Double(try create(frames[i % frames.count], direct: direct).size().width) } }
            }
            // Includes production Data copy and alpha normalization; excludes
            // synthetic generation and readback. Alternate order between trials.
            for trial in 0..<4 {
                for direct in trial % 2 == 0 ? [false, true] : [true, false] {
                    var durations = [Double](); durations.reserveCapacity(iterations)
                    for i in 0..<iterations {
                        let start = ProcessInfo.processInfo.systemUptime
                        try autoreleasepool {
                            let texture = try create(frames[i % frames.count], direct: direct)
                            checksum += Double(texture.size().width)
                        }
                        durations.append(ProcessInfo.processInfo.systemUptime - start)
                    }
                    benchmarkRows.append(["trial": trial, "method": direct ? "production-opaque-data" : "original-cgimage", "timing": samplesSummary(durations)])
                }
            }
            let unchanged = frames.map { sha($0.rgba) } == hashesBefore
            report["passed"] = passed && unchanged
            report["benchmark"] = ["rows": benchmarkRows, "iterationsPerTrial": iterations,
                "sourceFrames": frames.count, "sourceUnchanged": unchanged, "checksum": checksum,
                "scope": "Texture creation only, including temporary texture release. No CGImage readback, SKView snapshot, display presentation, game or audio. Not real-time acceptance." ]
        }
        try verifySources()
        let bytes = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
        try bytes.write(to: output.appendingPathComponent("report.json"), options: .atomic)
        print("Texture presentation passed=\(report["passed"]!) report=\(output.appendingPathComponent("report.json").path)")
        if report["passed"] as? Bool != true { exit(2) }
    }
}
