import AVFoundation

/// Streams the native stereo samples at the queried engine rate using the player's render timeline.
/// Speaker/device latency must never be mistaken for unrendered queue backlog.
final class VTAudioOutput {
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let format: AVAudioFormat
    private let enabled: Bool
    private let lock = NSLock()
    private let prebufferFrames: AVAudioFramePosition
    private let maximumPendingFrames: AVAudioFramePosition
    private var observer: NSObjectProtocol?
    private var started = false
    private var scheduledFrames: AVAudioFramePosition = 0
    private var retryAfter = Date.distantPast
    private var mutedValue: Bool
    private var flushCounts: [String: Int] = [:]
    private var engineStartCount = 0
    private var engineFailureCount = 0
    private var peakPendingFrames: AVAudioFramePosition = 0
    private var minPendingFrames: AVAudioFramePosition = .max
    private var hasRenderTap = false

    var muted: Bool {
        get { lock.lock(); defer { lock.unlock() }; return mutedValue }
        set {
            lock.lock(); defer { lock.unlock() }
            mutedValue = newValue
            engine.mainMixerNode.outputVolume = newValue ? 0 : 0.8
        }
    }

    init(muted: Bool, sampleRate: Double, enabled: Bool = true) {
        self.enabled = enabled
        format = AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: 2)!
        prebufferFrames = AVAudioFramePosition(sampleRate * 0.04)
        maximumPendingFrames = AVAudioFramePosition(sampleRate * 0.25)
        mutedValue = muted
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: format)
        engine.mainMixerNode.outputVolume = muted ? 0 : 0.8
        observer = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main) { [weak self] _ in
            guard let self else { return }
            self.lock.lock(); defer { self.lock.unlock() }
            self.flushLocked(reason: "configuration")
        }
        // Starting the device may be slow. Do it before the game/display clock
        // starts, not in the first produced game frame.
        _ = startEngineLocked()
    }

    deinit {
        if let observer { NotificationCenter.default.removeObserver(observer) }
        engine.stop()
        if hasRenderTap { player.removeTap(onBus: 0) }
    }

    private func startEngineLocked() -> Bool {
        guard enabled else { return false }
        if engine.isRunning { return true }
        guard Date() >= retryAfter else { return false }
        do {
            engine.prepare(); try engine.start()
            engineStartCount += 1
            return true
        } catch {
            engineFailureCount += 1
            retryAfter = Date().addingTimeInterval(5)
            fputs("Audio output unavailable: \(error.localizedDescription)\n", stderr)
            return false
        }
    }

    /// Nil until the player has started rendering. This clock excludes the
    /// downstream mixer/device presentation delay used by .dataPlayedBack.
    private func renderedFramesLocked() -> AVAudioFramePosition? {
        guard started, let nodeTime = player.lastRenderTime,
              let time = player.playerTime(forNodeTime: nodeTime),
              time.isSampleTimeValid, time.sampleRate > 0 else { return nil }
        return max(0, AVAudioFramePosition(Double(time.sampleTime) * format.sampleRate / time.sampleRate))
    }

    private func flushLocked(reason: String) {
        flushCounts[reason, default: 0] += 1
        player.stop() // unschedules old samples and resets player time to zero
        started = false
        scheduledFrames = 0
    }

    func flush() {
        lock.lock(); defer { lock.unlock() }
        flushLocked(reason: "host")
    }

    func present(_ samples: [Int16]) {
        let count = samples.count / 2
        guard count > 0 else { return }
        lock.lock(); defer { lock.unlock() }
        guard startEngineLocked() else { return }

        if let rendered = renderedFramesLocked() {
            let pending = scheduledFrames - rendered
            minPendingFrames = min(minPendingFrames, pending)
            // A real producer stall leaves the player's timeline ahead of our
            // scheduled samples. Re-prime once instead of scheduling in its past.
            if pending < 0 { flushLocked(reason: "underrun") }
            else if pending > maximumPendingFrames { flushLocked(reason: "backlog") }
        }

        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count)),
              let channels = buffer.floatChannelData else { return }
        buffer.frameLength = AVAudioFrameCount(count)
        for index in 0..<count {
            channels[0][index] = Float(samples[index * 2]) / 32768
            channels[1][index] = Float(samples[index * 2 + 1]) / 32768
        }
        // Explicit contiguous sample times; no late completion callbacks or
        // main-thread callbacks participate in queue accounting.
        player.scheduleBuffer(buffer, at: AVAudioTime(sampleTime: scheduledFrames, atRate: format.sampleRate), options: [], completionHandler: nil)
        scheduledFrames += AVAudioFramePosition(count)
        let pending = scheduledFrames - (renderedFramesLocked() ?? 0)
        peakPendingFrames = max(peakPendingFrames, pending)
        if !started && scheduledFrames >= prebufferFrames {
            player.play()
            started = true
        }
    }

    /// Bounded playback telemetry for real-device regression checks.
    func diagnostics() -> [String: Any] {
        lock.lock(); defer { lock.unlock() }
        let rendered = renderedFramesLocked() ?? 0
        let underruns = flushCounts["underrun", default: 0]
        let backlogs = flushCounts["backlog", default: 0]
        return ["sourceSampleRate": format.sampleRate, "automaticRecoveryCount": underruns + backlogs,
                "underrunCount": underruns, "backlogRecoveryCount": backlogs,
                "flushCounts": flushCounts, "engineStartCount": engineStartCount,
                "engineFailureCount": engineFailureCount, "pendingFrames": scheduledFrames - rendered,
                "peakPendingFrames": peakPendingFrames,
                "minPendingFrames": minPendingFrames == .max ? 0 : minPendingFrames,
                "renderedFrames": rendered, "scheduledFrames": scheduledFrames,
                "outputSampleRate": engine.outputNode.outputFormat(forBus: 0).sampleRate,
                "outputLatencySeconds": engine.outputNode.presentationLatency,
                "isRunning": engine.isRunning, "isPlaying": player.isPlaying]
    }

    // Diagnostic capture taps only this game's rendered PCM, never a microphone
    // or other applications. Normal playback installs no tap.
    // A tap must not call back into this object; copy into separate bounded storage.
    func installRenderTap(bufferSize: AVAudioFrameCount = 1024, block: @escaping AVAudioNodeTapBlock) {
        lock.lock(); defer { lock.unlock() }
        precondition(!hasRenderTap)
        player.installTap(onBus: 0, bufferSize: bufferSize, format: format, block: block)
        hasRenderTap = true
    }

    func removeRenderTap() {
        lock.lock(); defer { lock.unlock() }
        if hasRenderTap { player.removeTap(onBus: 0); hasRenderTap = false }
    }
}
