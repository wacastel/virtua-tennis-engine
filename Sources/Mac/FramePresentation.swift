import Foundation

/// Presentation only. Native input, frame, audio and emulated clocks never read it.
struct VTPresentationMarker {
    private(set) var lastFrame: Int?
    func needsPresentation(frame: Int, available: Bool) -> Bool { available && lastFrame != frame }
    mutating func presented(frame: Int) { lastFrame = frame }
    mutating func reset() { lastFrame = nil }
}
