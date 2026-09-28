import AppKit
import SpriteKit
import GameController

final class VTScene: SKScene {
    private let game: VTGame
    private let audio: VTAudioOutput
    let controls = VTControls()
    private let picture = SKSpriteNode(color: .black, size: CGSize(width: 512, height: 384))
    private let shade = SKShapeNode(rectOf: CGSize(width: 512, height: 384))
    private let title = SKLabelNode(fontNamed: "AvenirNext-DemiBold")
    private let hint = SKLabelNode(fontNamed: "AvenirNext-Regular")
    private var clock = VTFrameClock()
    private let pacingQueue = VTFrameContinuation()
    private var presentationMarker = VTPresentationMarker()
    private var failed = false
    private enum UpdateSource { case display, continuation }
    private var updateCount = 0
    private var displayUpdateCount = 0
    private var continuationUpdateCount = 0
    private var updateWallSeconds: TimeInterval = 0
    private var maximumUpdateSeconds: TimeInterval = 0
    private var maximumUpdateGapSeconds: TimeInterval = 0
    private var maximumDisplayGapSeconds: TimeInterval = 0
    private var previousUpdateStart: TimeInterval?
    private var previousDisplayStart: TimeInterval?
    private var presentationCount = 0
    private var updatesOnMainThread = true
    private var lastInput = VTInput()
    private var observers: [(NotificationCenter, NSObjectProtocol)] = []
    private(set) var pausedByHost = false
    var onStatus: ((String) -> Void)?
    var diagnosticReplay: VTReplay?
    var savesPreferences = true
    var frameCount: Int { game.frameCount }
    var muted: Bool {
        get { audio.muted }
        set { audio.muted = newValue; if savesPreferences { VTPreferences.defaults.set(newValue, forKey: VTPreferences.mutedKey) } }
    }
    init(muted: Bool) throws {
        game = try VTGame()
        audio = VTAudioOutput(muted: muted, sampleRate: Double(game.sampleRate))
        super.init(size: CGSize(width: 512, height: 384))
        scaleMode = .aspectFit; backgroundColor = .black
        picture.position = CGPoint(x: 256, y: 192); addChild(picture)
        shade.position = picture.position; shade.fillColor = NSColor.black.withAlphaComponent(0.78)
        shade.strokeColor = .clear; shade.zPosition = 10; shade.isHidden = true; addChild(shade)
        title.fontSize = 24; title.position = CGPoint(x: 0, y: 9); shade.addChild(title)
        hint.fontSize = 12; hint.position = CGPoint(x: 0, y: -20); shade.addChild(hint)
        controls.onTogglePause = { [weak self] in self?.togglePause() }
        controls.onResume = { [weak self] in self?.setPaused(false) }
        _ = controls.refreshControllers(GCController.controllers())
        observe(.GCControllerDidConnect) { [weak self] _ in self?.refreshControllers() }
        observe(.GCControllerDidDisconnect) { [weak self] _ in self?.refreshControllers() }
        for name in [NSWorkspace.willSleepNotification, NSWorkspace.didWakeNotification] {
            observe(name, center: NSWorkspace.shared.notificationCenter) { [weak self] _ in self?.setPaused(true) }
        }
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) is unavailable") }
    deinit { for (center, observer) in observers { center.removeObserver(observer) } }
    private func observe(_ name: Notification.Name, center: NotificationCenter = .default, callback: @escaping (Notification) -> Void) {
        observers.append((center, center.addObserver(forName: name, object: nil, queue: .main, using: callback)))
    }
    private func refreshControllers() {
        if controls.refreshControllers(GCController.controllers()) { setPaused(true) }
    }
    func key(_ key: UInt16, down: Bool, repeated: Bool = false) { controls.key(key, down: down, repeated: repeated) }
    func clearKeyboard() { controls.clearKeyboard() }
    func setInputActive(_ active: Bool) { controls.setActive(active); if !active { setPaused(true) } }
    func setPaused(_ paused: Bool) {
        pacingQueue.cancel()
        pausedByHost = paused; controls.setPaused(paused); clock.reset(); audio.flush()
        shade.isHidden = !paused; title.text = "Paused"; hint.text = "Release controls, then press Create / Options / Return"
    }
    func togglePause() { setPaused(!pausedByHost) }
    func resetGame() {
        pacingQueue.cancel()
        do { try game.reset(); failed = false; picture.texture = nil; presentationMarker.reset(); setPaused(false) }
        catch { showFailure(error) }
    }
    override func update(_ _: TimeInterval) { pacedUpdate(source: .display) }
    private func pacedUpdate(source: UpdateSource) {
        precondition(Thread.isMainThread)
        // Both event sources use one monotonic clock; SpriteKit's timestamp must
        // not be mixed with the continuation timer's clock epoch.
        let time = ProcessInfo.processInfo.systemUptime
        pacingQueue.cancel()
        if let previousUpdateStart { maximumUpdateGapSeconds = max(maximumUpdateGapSeconds, time - previousUpdateStart) }
        previousUpdateStart = time
        switch source {
        case .display:
            displayUpdateCount += 1
            if let previousDisplayStart { maximumDisplayGapSeconds = max(maximumDisplayGapSeconds, time - previousDisplayStart) }
            previousDisplayStart = time
        case .continuation: continuationUpdateCount += 1
        }
        var continuationEligible = false
        defer {
            // Also runs on a count-zero display after continuation work. Texture
            // cost is included before full end-of-chunk clock accounting.
            if source == .display, !failed,
               presentationMarker.needsPresentation(frame: game.frameCount, available: game.latestFrame != nil),
               let image = game.latestFrame?.image {
                let texture = SKTexture(cgImage: image)
                texture.filteringMode = .nearest; picture.texture = texture
                presentationMarker.presented(frame: game.frameCount)
                presentationCount += 1
            }
            let ended = ProcessInfo.processInfo.systemUptime
            if continuationEligible {
                // Credit this chunk's real elapsed time exactly once. Next entry
                // sees only the idle interval after this end timestamp.
                let accounted = clock.update(ended)
                if !accounted { audio.flush(); continuationEligible = false }
                else if clock.discardExcess() { audio.flush(); continuationEligible = false }
            }
            let active = continuationEligible && controls.isActive && !pausedByHost && !failed
            pacingQueue.schedule(balance: clock.balance, eligible: active) { [weak self] in
                guard let self, self.controls.isActive, !self.pausedByHost, !self.failed else { return }
                self.pacedUpdate(source: .continuation)
            }
            let duration = ProcessInfo.processInfo.systemUptime - time
            updateWallSeconds += duration
            maximumUpdateSeconds = max(maximumUpdateSeconds, duration)
        }
        updateCount += 1; updatesOnMainThread = updatesOnMainThread && Thread.isMainThread
        controls.pollController()
        guard controls.isActive, !pausedByHost, !failed else { clock.reset(); return }
        let oldGaps = clock.discardedGaps
        guard clock.update(time) else { if clock.discardedGaps != oldGaps { audio.flush() }; return }
        continuationEligible = true
        do {
            // Yield to the main run loop after each indivisible native step.
            if clock.wantsStep {
                let input = diagnosticReplay?.input(frame: game.frameCount) ?? controls.input()
                lastInput = input
                let before = game.emulatedSeconds
                audio.present(try game.advance(input)); controls.consumedFrame()
                try clock.consume(game.emulatedSeconds - before)
            }
            // Backlog recovery and display presentation run at chunk exit.
        } catch { showFailure(error) }
    }
    private func showFailure(_ error: Error) {
        pacingQueue.cancel()
        failed = true; audio.flush(); controls.clear()
        shade.isHidden = false; title.text = "Game stopped"; hint.text = "Use Game → Reset to restart"
        onStatus?(error.localizedDescription); fputs("Virtua Tennis: \(error.localizedDescription)\n", stderr)
    }
    func shutdown() { setPaused(true); failed = true; game.close() }
    func capture(to url: URL) throws {
        guard let frame = game.latestFrame else { throw VirtuaTennisError.message("No engine frame is available.") }
        try frame.writePNG(to: url)
    }
    func diagnostics() -> [String: Any] {
        var value = audio.diagnostics()
        value["gameState"] = game.diagnostics(); value["gameFrames"] = game.frameCount
        value["sceneUpdates"] = updateCount; value["displayUpdates"] = displayUpdateCount
        value["continuationUpdates"] = continuationUpdateCount; value["texturePresentations"] = presentationCount
        value["sceneUpdateWallSeconds"] = updateWallSeconds; value["maximumSceneUpdateSeconds"] = maximumUpdateSeconds
        value["maximumSceneUpdateGapSeconds"] = maximumUpdateGapSeconds
        value["maximumDisplayUpdateGapSeconds"] = maximumDisplayGapSeconds
        value["updatesOnMainThread"] = updatesOnMainThread
        value["discardedClockGaps"] = clock.discardedGaps; value["clockBalanceSeconds"] = clock.balance; value["pausedByHost"] = pausedByHost
        value["lastInput"] = lastInput.diagnostic
        return value
    }
}
