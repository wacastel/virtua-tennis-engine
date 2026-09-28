import Foundation
import SpriteKit

/// The arcade display is opaque; raw framebuffer alpha remains in replay data.
/// Copy only for presentation, avoiding the CGImage conversion on every upload.
func makeVTFrameTexture(rgba: Data, width: Int, height: Int) -> SKTexture? {
    guard (1...2048).contains(width), (1...2048).contains(height),
          rgba.count == width * height * 4 else { return nil }
    var opaque = rgba
    opaque.withUnsafeMutableBytes { (raw: UnsafeMutableRawBufferPointer) in
        let bytes = raw.bindMemory(to: UInt8.self)
        for index in stride(from: 3, to: bytes.count, by: 4) { bytes[index] = 255 }
    }
    // Bridge rows are top-down. SpriteKit's data initializer expects the flip
    // to match the existing DeviceRGB/noneSkipLast CGImage presentation.
    let texture = SKTexture(data: opaque, size: CGSize(width: width, height: height), flipped: true)
    texture.filteringMode = .nearest
    return texture
}

/// Presentation only. Native input, frame, audio and emulated clocks never read it.
struct VTPresentationMarker {
    private(set) var lastFrame: Int?
    func needsPresentation(frame: Int, available: Bool) -> Bool { available && lastFrame != frame }
    mutating func presented(frame: Int) { lastFrame = frame }
    mutating func reset() { lastFrame = nil }
}
