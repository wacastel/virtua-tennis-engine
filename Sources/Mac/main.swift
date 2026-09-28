import AppKit
import SpriteKit
import GameController

private let arguments = CommandLine.arguments
setbuf(stdout, nil)
private func argument(_ name: String) -> String? {
    guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else { return nil }
    let result = arguments[index + 1]
    return result.hasPrefix("--") ? nil : result
}
private func replay(_ path: String) throws -> VTReplay {
    let value = try JSONDecoder().decode(VTReplay.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
    try value.validate(); return value
}
private func report(_ value: [String: Any], path: String? = nil) throws {
    let data = try JSONSerialization.data(withJSONObject: value, options: [.prettyPrinted, .sortedKeys])
    if let path { try data.write(to: URL(fileURLWithPath: path), options: .atomic) }
    print(String(decoding: data, as: UTF8.self))
}
if arguments.contains("--help") {
    print("""
    Virtua Tennis — macOS host for the fixed NAOMI engine
    Launch without arguments to play using verified bundled media.
    --assets DIRECTORY / --save-dir DIRECTORY   Override media or isolated game-data path
    --fullscreen / --mute / --sound             Presentation and audio options
    --controllers                              List connected controllers
    --headless --frames N                       Execute real engine presentation steps without a window
    --diagnostic-run FILE.json                  Presentation-indexed digital two-player inputs
    --self-test --frames N                      Compare graphics/PCM from two fresh real-engine sessions
    --diagnostic-capture FILE.png               Capture final headless engine frame
    --diagnostic-report FILE.json               Write headless JSON report
    --diagnostic-frames FILE.jsonl              Per-step RGBA/PCM/count/time evidence
    --audio-replay FILE.json                    Replay tennis inputs in the visible app
    --audio-report FILE.json                    Save playback telemetry when the app closes
    --capture FILE.png --capture-after SECONDS  Capture the engine picture during a GUI run
    --quit-after SECONDS                        Close after a bounded GUI interval
    Inputs: buttons is a held digital mask; reserved bits 6 and 14 must be zero.
    Player byte: Up1 Down2 Left4 Right8 Shot16 Lob32 Start128.
    Player2 uses the same byte shifted8; Coin1=65536, Coin2=131072.
    Each PS5: D-pad/left stick moves; Cross shoots, Circle lobs.
    L1 inserts that player's coin; Options starts. Triangle/I are unused.
    P1 keyboard: arrows, Z/X, 5 coin, 1/Return Start. P2: WASD, F/G, 6 coin, 2 Start.
    Create (left of the touchpad)/P/Escape pauses or resumes the host.
    Options also resumes when paused. Up to two controllers retain their player assignments.
    """)
    exit(0)
}
if arguments.contains("--controllers") {
    _ = NSApplication.shared
    GCController.shouldMonitorBackgroundEvents = true
    GCController.startWirelessControllerDiscovery(completionHandler: nil)
    RunLoop.current.run(until: Date().addingTimeInterval(2)); GCController.stopWirelessControllerDiscovery()
    let list = GCController.controllers().map { ["name": $0.vendorName ?? $0.productCategory,
        "category": $0.productCategory, "extendedGamepad": $0.extendedGamepad != nil,
        "dualSense": $0.extendedGamepad is GCDualSenseGamepad] as [String: Any] }
    try report(["game": "Virtua Tennis", "connectedCount": list.count, "controllers": list,
                "physicalActuationTested": false])
    exit(0)
}
if arguments.contains("--headless") || arguments.contains("--self-test") || arguments.contains("--diagnostic-run") {
    do {
        let route = try argument("--diagnostic-run").map(replay)
        let frames = argument("--frames").flatMap(Int.init) ?? route?.frames ?? 3600
        guard (1...1_000_000).contains(frames) else { throw VirtuaTennisError.message("Steps must be between 1 and 1000000.") }
        var first = try runVTReplay(frames: frames, replay: route,
                    capture: argument("--diagnostic-capture").map { URL(fileURLWithPath: $0) },
                    trace: argument("--diagnostic-frames").map { URL(fileURLWithPath: $0) })
        if arguments.contains("--self-test") {
            let second = try runVTReplay(frames: frames, replay: route, capture: nil)
            for key in ["pictureSHA256","audioSHA256","audioFrameSequenceSHA256","finalPictureSHA256"] {
                guard first[key] as? String == second[key] as? String else {
                    throw VirtuaTennisError.message("Fresh native sessions disagree on \(key).")
                }
            }
            guard first["sampleFrames"] as? Int == second["sampleFrames"] as? Int else {
                throw VirtuaTennisError.message("Fresh native sessions disagree on PCM length.")
            }
            first["deterministic"] = true; first["comparedRuns"] = 2
            first["scope"] = "Graphics and PCM determinism only; separate core validation is required for CPU and game semantics."
        }
        try report(first, path: argument("--diagnostic-report")); exit(0)
    } catch { fputs("Virtua Tennis: \(error.localizedDescription)\n", stderr); exit(1) }
}

final class VTView: SKView {
    override var acceptsFirstResponder: Bool { true }
    override func keyDown(with event: NSEvent) {
        if event.modifierFlags.contains(.command) { (scene as? VTScene)?.clearKeyboard(); super.keyDown(with: event); return }
        (scene as? VTScene)?.key(event.keyCode, down: true, repeated: event.isARepeat)
    }
    override func keyUp(with event: NSEvent) { (scene as? VTScene)?.key(event.keyCode, down: false) }
    override func flagsChanged(with event: NSEvent) { if event.modifierFlags.contains(.command) { (scene as? VTScene)?.clearKeyboard() } }
    override func resignFirstResponder() -> Bool { (scene as? VTScene)?.clearKeyboard(); return super.resignFirstResponder() }
}
final class VTApp: NSObject, NSApplicationDelegate, NSWindowDelegate, NSMenuItemValidation {
    private var window: NSWindow!
    private var view: VTView!
    private var scene: VTScene!
    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            scene = try VTScene(muted: !arguments.contains("--sound") && (arguments.contains("--mute") || VTPreferences.defaults.bool(forKey: VTPreferences.mutedKey)))
            scene.savesPreferences = argument("--audio-report") == nil && argument("--audio-replay") == nil
            if let path = argument("--audio-replay") { scene.diagnosticReplay = try replay(path) }
        } catch { NSAlert(error: error).runModal(); NSApp.terminate(nil); return }
        let available = NSScreen.main?.visibleFrame.size ?? CGSize(width: 1280, height: 900)
        let width = max(640, min(1024, available.width - 60, (available.height - 90) * 4 / 3))
        let size = NSSize(width: width, height: width * 3 / 4)
        window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                          styleMask: [.titled,.closable,.miniaturizable,.resizable], backing: .buffered, defer: false)
        window.title = "Virtua Tennis"; window.contentAspectRatio = NSSize(width: 4, height: 3)
        window.contentMinSize = NSSize(width: 512, height: 384); window.backgroundColor = .black
        window.isReleasedWhenClosed = false; window.delegate = self; window.collectionBehavior = [.fullScreenPrimary]
        view = VTView(frame: NSRect(origin: .zero, size: size)); view.autoresizingMask = [.width,.height]
        view.preferredFramesPerSecond = max(60, NSScreen.main?.maximumFramesPerSecond ?? 60)
        window.contentView = view
        scene.onStatus = { [weak self] in self?.window.subtitle = $0 }
        installMenus(); window.center(); window.makeKeyAndOrderFront(nil)
        view.presentScene(scene); window.makeFirstResponder(view); NSApp.activate(ignoringOtherApps: true)
        GCController.startWirelessControllerDiscovery(completionHandler: nil)
        if arguments.contains("--fullscreen") { window.toggleFullScreen(nil) }
        if let path = argument("--capture") {
            let delay = max(0, argument("--capture-after").flatMap(Double.init) ?? 10)
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { [weak self] in
                do { try self?.scene.capture(to: URL(fileURLWithPath: path)) }
                catch { fputs("Capture failed: \(error.localizedDescription)\n", stderr) }
            }
        }
        if let delay = argument("--quit-after").flatMap(Double.init), delay.isFinite, delay >= 0 {
            DispatchQueue.main.asyncAfter(deadline: .now() + delay) { NSApp.terminate(nil) }
        }
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationWillTerminate(_ notification: Notification) {
        GCController.stopWirelessControllerDiscovery()
        if let path = argument("--audio-report"), let scene {
            var value = scene.diagnostics()
            value["preferredDisplayFramesPerSecond"] = view.preferredFramesPerSecond
            value["windowVisible"] = window.isVisible; value["windowOccluded"] = !window.occlusionState.contains(.visible)
            do { try report(value, path: path) } catch { fputs("Playback report failed: \(error.localizedDescription)\n", stderr) }
        }
        scene?.shutdown()
    }
    func applicationDidResignActive(_ notification: Notification) { scene?.setInputActive(false) }
    func applicationDidBecomeActive(_ notification: Notification) { if window?.isKeyWindow == true { scene?.setInputActive(true) } }
    func windowDidBecomeKey(_ notification: Notification) { scene?.setInputActive(true) }
    func windowDidResignKey(_ notification: Notification) { scene?.setInputActive(false) }
    private func menuItem(_ title: String, _ action: Selector, _ key: String = "") -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self; item.keyEquivalentModifierMask = [.command]; return item
    }
    private func installMenus() {
        let menu = NSMenu(), app = NSMenu(title: "Virtua Tennis"), game = NSMenu(title: "Game")
        let window = NSMenu(title: "Window"), help = NSMenu(title: "Help")
        app.addItem(menuItem("About Virtua Tennis", #selector(about))); app.addItem(.separator())
        app.addItem(menuItem("Quit Virtua Tennis", #selector(quit), "q"))
        game.addItem(menuItem("Pause", #selector(pause))); game.addItem(menuItem("Reset Game", #selector(reset), "r"))
        game.addItem(menuItem("Mute", #selector(mute), "m"))
        window.addItem(menuItem("Enter Full Screen", #selector(fullscreen), "f"))
        help.addItem(menuItem("Controls", #selector(controls), "/"))
        for submenu in [app,game,window,help] { let item = NSMenuItem(); item.submenu = submenu; menu.addItem(item) }
        NSApp.mainMenu = menu; NSApp.windowsMenu = window; NSApp.helpMenu = help
    }
    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        if item.action == #selector(pause) { item.title = scene?.pausedByHost == true ? "Resume" : "Pause" }
        if item.action == #selector(mute) { item.state = scene?.muted == true ? .on : .off }
        return true
    }
    @objc private func quit() { NSApp.terminate(nil) }
    @objc private func pause() { scene.togglePause() }
    @objc private func reset() { scene.resetGame(); window.makeFirstResponder(view) }
    @objc private func mute() { scene.muted.toggle() }
    @objc private func fullscreen() { window.toggleFullScreen(nil) }
    @objc private func controls() {
        scene.setPaused(true)
        let alert = NSAlert(); alert.messageText = "Virtua Tennis Controls"
        alert.informativeText = "Each controller: left stick / D-pad moves\nShot: Cross · Lob: Circle\nCoin: L1 · Start: Options\n\nPlayer 1 keyboard: arrows move, Z/X shot/lob, 5 coin, 1 or Return Start\nPlayer 2 keyboard: WASD move, F/G shot/lob, 6 coin, 2 Start\n\nPause / resume: Create (small button left of touchpad) / P / Escape\nStart also resumes when paused.\n\nUp to two controllers retain their player assignments. Focus loss, sleep or an assigned controller disconnect pauses the app. Release held controls before resuming."
        alert.runModal(); window.makeFirstResponder(view)
    }
    @objc private func about() {
        scene.setPaused(true)
        let alert = NSAlert(); alert.messageText = "Virtua Tennis"
        alert.informativeText = "Apple Silicon edition\nOriginal NAOMI game © SEGA, 1999\n\nOpen Help → Controls for keyboard and DualSense mappings."
        alert.runModal(); window.makeFirstResponder(view)
    }
}
let app = NSApplication.shared
let delegate = VTApp()
app.setActivationPolicy(.regular); app.delegate = delegate; app.run()
