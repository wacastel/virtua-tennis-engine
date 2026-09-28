import Foundation
import GameController

// Virtua Tennis cabinet: SW1 Shot, SW2 Lob. The engine maps the stable
// per-player byte to original board ports. Host shortcuts never enter that byte.
final class VTControls {
    var onTogglePause: (() -> Void)?
    var onResume: (() -> Void)?
    private final class Slot {
        var controller: GCController?
        var needsNeutral = true, pause = false
        var current: UInt32 = 0, pending: UInt32 = 0
        func clear() { needsNeutral = true; pause = false; current = 0; pending = 0 }
    }
    private let slots = [Slot(), Slot()]
    var controllers: [GCController?] { slots.map { $0.controller } }
    private(set) var isActive = true
    private(set) var paused = false
    private var pressed = Set<UInt16>(), pendingKeys = Set<UInt16>()
    // macOS hardware key codes: arrows/ZX/1/Return/5; WASD/FG/2/6.
    private let keyMap: [(UInt16, UInt32)] = [
        (126,VTButton.up),(125,VTButton.down),(123,VTButton.left),(124,VTButton.right),
        (6,VTButton.shot),(7,VTButton.lob),
        (18,VTButton.start),(36,VTButton.start),(76,VTButton.start),(23,VTButton.coin1),
        (13,VTButton.up << 8),(1,VTButton.down << 8),(0,VTButton.left << 8),(2,VTButton.right << 8),
        (3,VTButton.shot << 8),(5,VTButton.lob << 8),
        (19,VTButton.start << 8),(22,VTButton.coin2)
    ]
    /// Preserve surviving assignments, then fill vacancies. An ignored third
    /// controller has no effect on active inputs or neutral guards.
    func refreshControllers(_ available: [GCController]) -> Bool {
        let usable = available.filter { $0.extendedGamepad != nil }
        var lostAssigned = false
        for slot in slots {
            if let current = slot.controller, !usable.contains(where: { $0 === current }) {
                current.playerIndex = .indexUnset; slot.controller = nil; slot.clear(); lostAssigned = true
            }
        }
        for controller in usable where !slots.contains(where: { $0.controller === controller }) {
            guard let index = slots.firstIndex(where: { $0.controller == nil }) else { break }
            let slot = slots[index]; slot.controller = controller; slot.clear()
            controller.playerIndex = index == 0 ? .index1 : .index2
            controller.extendedGamepad?.buttonMenu.preferredSystemGestureState = .disabled
            controller.extendedGamepad?.buttonOptions?.preferredSystemGestureState = .disabled
        }
        return lostAssigned
    }
    func clear() { clearKeyboard(); for slot in slots { slot.clear() } }
    func clearKeyboard() { pressed.removeAll(); pendingKeys.removeAll() }
    func setActive(_ value: Bool) { isActive = value; clear() }
    func setPaused(_ value: Bool) { paused = value; clear() }
    func key(_ code: UInt16, down: Bool, repeated: Bool = false) {
        guard isActive, !repeated else { return }
        if !down { pressed.remove(code); return }
        let fresh = pressed.insert(code).inserted
        if code == 35 || code == 53 { if fresh { onTogglePause?() }; return }
        if paused && [18,19,36,76].contains(code) { if fresh { onResume?() }; return }
        if keyMap.contains(where: { $0.0 == code }) { pendingKeys.insert(code) }
    }
    func pollController() {
        guard isActive else { return }
        for (index, slot) in slots.enumerated() {
            guard let pad = slot.controller?.extendedGamepad else { continue }
            var x = pad.leftThumbstick.xAxis.value, y = pad.leftThumbstick.yAxis.value
            if max(abs(pad.dpad.xAxis.value),abs(pad.dpad.yAxis.value)) > 0.3 {
                x = pad.dpad.xAxis.value; y = pad.dpad.yAxis.value
            }
            var buttons: UInt32 = 0
            if x < -0.3 { buttons |= VTButton.left }; if x > 0.3 { buttons |= VTButton.right }
            if y < -0.3 { buttons |= VTButton.down }; if y > 0.3 { buttons |= VTButton.up }
            if pad.buttonA.isPressed { buttons |= VTButton.shot }
            if pad.buttonB.isPressed { buttons |= VTButton.lob }
            if pad.buttonMenu.isPressed { buttons |= VTButton.start }
            buttons <<= UInt32(index * 8)
            if pad.leftShoulder.isPressed { buttons |= index == 0 ? VTButton.coin1 : VTButton.coin2 }
            // DualSense Create (left of the touchpad); pressing the movement
            // stick must never interrupt a rally.
            let pause = pad.buttonOptions?.isPressed == true
            if slot.needsNeutral {
                if buttons == 0 && !pause { slot.needsNeutral = false }
                slot.pause = pause; slot.current = 0; continue
            }
            if pause && !slot.pause { onTogglePause?(); return }
            slot.pause = pause
            if paused && buttons & (VTButton.start << UInt32(index * 8)) != 0 { onResume?(); return }
            slot.current = paused ? 0 : buttons
            if !paused { slot.pending |= buttons }
        }
    }
    func input() -> VTInput {
        guard isActive, !paused else { return VTInput() }
        let keys = pressed.union(pendingKeys)
        var buttons = slots.reduce(UInt32(0)) { $0 | $1.current | $1.pending }
        for (code, bit) in keyMap where keys.contains(code) { buttons |= bit }
        return VTInput(buttons: VTButton.normalize(buttons))
    }
    // Consume pending taps only after an original engine frame, not a display poll.
    func consumedFrame() { pendingKeys.removeAll(); for slot in slots { slot.pending = 0 } }
}
