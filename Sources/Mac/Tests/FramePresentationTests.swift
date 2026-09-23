import Foundation

func testFramePresentation() throws {
    var marker = VTPresentationMarker()
    try check(!marker.needsPresentation(frame: 0, available: false), "Missing startup image never uploads")
    try check(marker.lastFrame == nil, "Missing image does not mark a frame as presented")
    try check(marker.needsPresentation(frame: 1, available: true), "Completed continuation image waits for a display")
    marker.presented(frame: 1)
    try check(!marker.needsPresentation(frame: 1, available: true), "Repeated zero-step display avoids reupload")
    try check(marker.needsPresentation(frame: 4, available: true) && marker.lastFrame == 1,
              "Unsuccessful image construction leaves the latest frame retryable")
    marker.presented(frame: 4); marker.reset()
    try check(marker.needsPresentation(frame: 4, available: true), "Reset permits a reused frame number")
    var latest: Int?, reference: Int?, presented: Int?
    var frame = 0, eagerUploads = 0, displayUploads = 0
    marker.reset()
    func native(_ steps: Int) {
        for _ in 0..<steps { frame += 1; latest = frame; reference = frame; eagerUploads += 1 }
    }
    func display(_ steps: Int) throws {
        native(steps)
        if marker.needsPresentation(frame: frame, available: latest != nil) {
            presented = latest; marker.presented(frame: frame); displayUploads += 1
        }
        try check(presented == reference, "External display sees the latest completed native image")
    }
    try display(0); native(3); try display(0); try display(0); try display(1); native(8); try display(0)
    try check(eagerUploads == 12 && displayUploads == 3, "Only overwritten intermediate texture uploads are removed")
    try check(frame == 12 && latest == 12, "Presentation marker does not change native step order")
    marker.reset(); latest = nil; reference = nil; presented = nil; frame = 0
    try display(0); native(1); try display(0)
    try check(presented == 1, "First picture after reset is presented")
}
