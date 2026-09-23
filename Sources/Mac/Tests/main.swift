import Foundation
import GameController

struct CheckError: Error, CustomStringConvertible { let description: String }
var checks = [String]()
func check(_ value: @autoclosure () -> Bool, _ description: String) throws {
    guard value() else { throw CheckError(description: description) }; checks.append(description)
}
func rejects(_ work: () throws -> Void, _ description: String) throws {
    do { try work() } catch { checks.append(description); return }; throw CheckError(description: description)
}
do {
    try VTInput(buttons: VTButton.all).validate()
    try rejects({ try VTInput(buttons: 1 << 18).validate() }, "Reject undefined arcade flags")
    let neutral = try JSONDecoder().decode(VTInput.self, from: Data("{}".utf8))
    try check(neutral == VTInput(), "Omitted controls are neutral")
    for bit: UInt32 in [1 << 6, 1 << 14, 1 << 18, 1 << 31] {
        try rejects({ try VTInput(buttons:bit).validate() }, "Reject reserved/unknown bit \(bit)")
    }
    let route = try JSONDecoder().decode(VTReplay.self, from: Data(#"{"steps":[{"frames":2,"buttons":65536},{"frames":1,"buttons":8192},{"frames":1,"buttons":16}]}"#.utf8))
    try route.validate()
    try check(route.frames == 4 && route.input(frame:0).buttons == VTButton.coin1 && route.input(frame:1).buttons == VTButton.coin1 && route.input(frame:2).buttons == VTButton.lob << 8 && route.input(frame:3).buttons == VTButton.shot && route.input(frame:4) == VTInput(), "Replay transitions retain exact per-player masks")
    for bad in [#"{"steps":[]}"#,#"{"steps":[{"frames":0}]}"#,#"{"steps":[{"frames":1000000},{"frames":1}]}"#,#"{"steps":[{"frames":1,"buttons":64}]}"#] {
        try rejects({ try JSONDecoder().decode(VTReplay.self, from: Data(bad.utf8)).validate() }, "Reject invalid replay \(bad)")
    }
    let value = VTInput(buttons: VTButton.shot | VTButton.lob << 8)
    let decoded = try JSONDecoder().decode(VTInput.self, from: JSONEncoder().encode(value))
    try check(decoded == value, "Replay encoding preserves two-player shot/lob")
    let c = VTControls()
    var pauses = 0, resumes = 0
    c.onTogglePause = { pauses += 1; c.setPaused(!c.paused) }
    c.onResume = { resumes += 1; c.setPaused(false) }
    for key: UInt16 in [34,17,8,4] { c.key(key,down:true); c.key(key,down:false) }
    try check(c.input() == VTInput(), "I/T and former third-action keys are unused")
    for (key,mask): (UInt16,UInt32) in [(126,1),(125,2),(123,4),(124,8),(6,16),(7,32),(18,128),(36,128),(76,128),(23,65536),(13,256),(1,512),(0,1024),(2,2048),(3,4096),(5,8192),(19,32768),(22,131072)] {
        c.key(key,down:true); c.key(key,down:false)
        try check(c.input().buttons == mask, "Keyboard \(key) produces \(mask), retaining a short tap")
        c.consumedFrame(); try check(c.input().buttons == 0, "Consumed keyboard \(key) tap clears")
    }
    for key: UInt16 in [126,124,13,0,6,7,3,5] { c.key(key,down:true) }
    try check(c.input().buttons == 57 + (53 << 8), "Independent keyboard diagonals and combined Shot/Lob")
    for key: UInt16 in [125,123,1,2] { c.key(key,down:true) }
    try check(c.input().buttons == 48 + (48 << 8), "Opposed directions cancel per player without dropping attacks")
    c.setActive(false); c.key(34,down:true)
    try check(c.input() == VTInput(), "Inactive window clears input and ignores shortcuts")
    c.setActive(true); c.key(35,down:true)
    try check(c.paused && pauses == 1, "P pauses")
    c.key(19,down:true)
    try check(!c.paused && resumes == 1 && c.input() == VTInput(), "Player-two Start resumes without leaking an arcade Start")
    let first = GCController.withExtendedGamepad(), second = GCController.withExtendedGamepad(), third = GCController.withExtendedGamepad()
    let a = first.extendedGamepad!, b = second.extendedGamepad!, d = third.extendedGamepad!
    a.buttonA.setValue(1); b.buttonY.setValue(1)
    try check(!c.refreshControllers([first,second]) && first.playerIndex == .index1 && second.playerIndex == .index2, "Two controllers receive stable player slots")
    c.pollController(); try check(c.input() == VTInput(), "Assigned active controls require neutral; unused Triangle has no action")
    a.buttonA.setValue(0); b.buttonY.setValue(0); a.rightShoulder.setValue(1); c.pollController()
    for (pad,shift,coin) in [(a,UInt32(0),VTButton.coin1),(b,UInt32(8),VTButton.coin2)] {
        for (button,mask) in [(pad.buttonA,VTButton.shot << shift),(pad.buttonB,VTButton.lob << shift),(pad.buttonMenu,VTButton.start << shift),(pad.leftShoulder,coin)] {
            button.setValue(1); c.pollController(); button.setValue(0); c.pollController()
            try check(c.input().buttons == mask, "Controller player \(shift/8+1) short tap retains bit \(mask)")
            c.consumedFrame(); try check(c.input().buttons == 0, "Consumed controller bit \(mask) clears")
        }
    }
    try check(c.input() == VTInput(), "Unused R1 neither enters arcade bits nor gates original controls")
    a.rightShoulder.setValue(0)
    a.leftThumbstick.setValueForXAxis(1,yAxis:1); b.leftThumbstick.setValueForXAxis(-1,yAxis:-1)
    a.buttonA.setValue(1); b.buttonB.setValue(1); c.pollController()
    try check(c.input().buttons == 25 + (38 << 8), "Two controller movement and attack masks remain independent")
    c.consumedFrame(); a.leftThumbstick.setValueForXAxis(0.2,yAxis:-0.2); a.buttonA.setValue(0); b.leftThumbstick.setValueForXAxis(0,yAxis:0); b.buttonB.setValue(0); c.pollController()
    try check(c.input().buttons == 0, "Digital stick deadzone")
    a.leftThumbstick.setValueForXAxis(-1,yAxis:1); a.dpad.setValueForXAxis(1,yAxis:0); c.pollController()
    try check(c.input().buttons == VTButton.right, "Active D-pad overrides both stick axes")
    c.consumedFrame(); a.leftThumbstick.setValueForXAxis(0,yAxis:0)
    try check(!c.refreshControllers([third,second,first]) && c.controllers[0] === first && c.controllers[1] === second, "An extra pad cannot reorder active players")
    c.pollController(); try check(c.input().buttons == VTButton.right, "Ignored extra connection preserves held movement")
    try check(!c.refreshControllers([first,second]), "Ignored extra removal is not an assigned disconnect")
    c.pollController(); try check(c.input().buttons == VTButton.right, "Ignored extra removal preserves held movement")
    a.dpad.setValueForXAxis(0,yAxis:0); c.pollController(); c.consumedFrame()
    for pad in [a,b] {
        pad.buttonY.setValue(1); c.pollController(); c.pollController()
        try check(c.input() == VTInput(), "Triangle is unused")
        pad.buttonY.setValue(0); c.pollController()
    }
    b.buttonA.setValue(1); c.pollController(); c.consumedFrame()
    try check(c.refreshControllers([second]) && c.controllers[0] == nil && c.controllers[1] === second && second.playerIndex == .index2, "P1 disconnect preserves P2 slot")
    c.pollController(); try check(c.input().buttons == VTButton.shot << 8, "Surviving P2 remains in the high byte")
    d.buttonB.setValue(1); try check(!c.refreshControllers([second,third]) && c.controllers[0] === third, "New pad fills the lowest vacant slot")
    c.pollController(); try check(c.input().buttons == VTButton.shot << 8, "New held P1 is gated without resetting P2")
    d.buttonB.setValue(0); c.pollController(); d.buttonB.setValue(1); c.pollController()
    try check(c.input().buttons == VTButton.lob | (VTButton.shot << 8), "Replacement P1 and existing P2 operate independently")
    c.setActive(false); c.setActive(true); c.pollController()
    try check(c.input() == VTInput(), "Focus return requires released active controller controls")
    d.buttonB.setValue(0); b.buttonA.setValue(0); c.pollController()
    d.buttonA.setValue(1); c.pollController()
    try check(c.input().buttons == VTButton.shot, "Releasing focus gate restores Shot")
    var clock = VTFrameClock(), advanced = 0.0, calls = 0
    _ = clock.update(0)
    let intervals = [0.0501, 0.0501, 0.01672, 0.03345, 0.01672]
    for tick in 1...2400 {
        _ = clock.update(Double(tick) / 120)
        while clock.wantsStep {
            let interval = intervals[calls % intervals.count]
            try clock.consume(interval); advanced += interval; calls += 1
        }
    }
    try check(advanced >= 20 && advanced - 20 < 0.0501, "Variable 50/33/17ms presentations track twenty seconds at a 120Hz display")
    try check(calls < 1200 && clock.discardedGaps == 0, "Presentation call count is independent of nominal display vblanks")
    let debt = clock.balance
    try check(debt <= 0 && debt > -0.0501, "A long engine step retains its time debt instead of accelerating boot")
    _ = clock.update(22)
    try check(!clock.wantsStep && clock.discardedGaps == 1, "A long wall-clock gap is discarded without fast-forwarding")
    _ = clock.update(21)
    try check(clock.discardedGaps == 2, "A backwards display clock is rejected")
    clock.reset(); _ = clock.update(30)
    try check(!clock.wantsStep && clock.balance == 0, "Pause/reset removes old timing debt")
    for invalid in [0.0, -0.1, Double.nan, Double.infinity, 1.0] {
        try rejects({ try clock.consume(invalid) }, "Reject malformed engine clock interval \(invalid)")
    }
    try testFrameScheduling()
    try testFramePresentation()
    let result: [String:Any] = ["passed":true,"checkCount":checks.count,"checks":checks,"engineLinked":false,
        "physicalControllerActuationTested":false,"scope":"Actual host input/router, variable-time clock, continuation queue and presentation ordering with keyboard events and Apple synthetic extended gamepads. No engine, ROM, GUI or audio is executed; tennis/game semantics require separate engine acceptance."]
    print(String(decoding:try JSONSerialization.data(withJSONObject:result,options:[.prettyPrinted,.sortedKeys]),as:UTF8.self))
} catch { fputs("Host input test failed: \(error)\n",stderr); exit(1) }
