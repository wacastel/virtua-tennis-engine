// SPDX-License-Identifier: GPL-2.0-only
// Development-only offscreen test. The shipping scene, clock and audio classes
// are compiled unchanged; no visible view or synthetic emulated-time source is used.
import AppKit
import CryptoKit
import Foundation
import Darwin

/// Diagnostic policy only. The real application and its clocks are unchanged.
private final class PlaybackScheduling {
    private var activity: NSObjectProtocol?
    private let originalClass: qos_class_t
    private let originalPriority: Int32
    private var changed = false
    private(set) var report: [String: Any]

    private static func snapshot() -> (qos_class_t, Int32, Int32, [String: Any]) {
        var qos = QOS_CLASS_UNSPECIFIED
        var priority: Int32 = 0
        let status = pthread_get_qos_class_np(pthread_self(), &qos, &priority)
        let label: String
        switch qos {
        case QOS_CLASS_USER_INTERACTIVE: label = "user-interactive"
        case QOS_CLASS_USER_INITIATED: label = "user-initiated"
        case QOS_CLASS_DEFAULT: label = "default"
        case QOS_CLASS_UTILITY: label = "utility"
        case QOS_CLASS_BACKGROUND: label = "background"
        default: label = "unspecified"
        }
        return (qos, priority, status, ["pthreadGetStatus": status, "pthreadRequestedClass": label,
            "pthreadRelativePriority": priority, "foundationQualityOfService": String(describing: Thread.current.qualityOfService),
            "foundationQualityOfServiceRawValue": Thread.current.qualityOfService.rawValue,
            "mainThread": Thread.isMainThread])
    }

    init(mode: String) throws {
        let before = Self.snapshot()
        originalClass = before.0; originalPriority = before.1
        report = ["mode": mode, "before": before.3, "changed": false,
            "scope": "Requested pthread QoS and Foundation thread properties; these do not measure effective scheduling priority or CPU-core placement."]
        if mode == "user-initiated" {
            guard Thread.isMainThread, before.2 == 0, originalClass != QOS_CLASS_UNSPECIFIED else {
                throw VirtuaTennisError.message("Cannot safely save and restore the main-thread QoS for this experiment.")
            }
            let status = pthread_set_qos_class_self_np(QOS_CLASS_USER_INITIATED, 0)
            guard status == 0 else { throw VirtuaTennisError.message("Cannot request user-initiated QoS (errno \(status)).") }
            changed = true
            activity = ProcessInfo.processInfo.beginActivity(options: .userInitiatedAllowingIdleSystemSleep,
                reason: "Bounded Virtua Tennis offscreen playback scheduling comparison")
            report["changed"] = true; report["pthreadSetStatus"] = status
            report["activity"] = "userInitiatedAllowingIdleSystemSleep"
        }
        report["afterRequest"] = Self.snapshot().3
    }

    @discardableResult func finish() -> Bool {
        if let activity { ProcessInfo.processInfo.endActivity(activity); self.activity = nil }
        var status: Int32 = 0
        if changed { status = pthread_set_qos_class_self_np(originalClass, originalPriority); changed = false }
        let after = Self.snapshot()
        let restored = status == 0 && after.2 == 0 && after.0 == originalClass && after.1 == originalPriority
        report["afterRestore"] = after.3; report["restoreStatus"] = status
        report["activityEnded"] = true; report["restored"] = restored
        return restored
    }
    deinit { if changed || activity != nil { finish() } }
}

@main
enum PlaybackHarness {
    static func digest(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    static func main() {
        setbuf(stdout, nil)
        do { try run() }
        catch { fputs("Playback harness: \(error.localizedDescription)\n", stderr); exit(1) }
    }

    static func run() throws {
        var options: [String: String] = [:]
        let arguments = Array(CommandLine.arguments.dropFirst())
        if arguments == ["--help"] {
            print("Usage: playback-harness --assets DIRECTORY --route FILE.json --out NEW_DIRECTORY [--seconds 120] [--hz 60] [--activity default|user-initiated]")
            return
        }
        let allowed = Set(["--assets", "--route", "--out", "--seconds", "--hz", "--activity"])
        guard arguments.count % 2 == 0 else { throw VirtuaTennisError.message("Options require values; use --help.") }
        for index in stride(from: 0, to: arguments.count, by: 2) {
            let key = arguments[index], value = arguments[index + 1]
            guard allowed.contains(key), options[key] == nil, !value.hasPrefix("--") else {
                throw VirtuaTennisError.message("Unknown, duplicate or incomplete option: \(key)")
            }
            options[key] = value
        }
        guard let assetPath = options["--assets"], let routePath = options["--route"], let outputPath = options["--out"] else {
            throw VirtuaTennisError.message("--assets, --route and --out are required.")
        }
        let duration = Double(options["--seconds"] ?? "120") ?? .nan
        let hz = Double(options["--hz"] ?? "60") ?? .nan
        let activityMode = options["--activity"] ?? "default"
        guard ["default", "user-initiated"].contains(activityMode) else {
            throw VirtuaTennisError.message("--activity must be default or user-initiated.")
        }
        guard duration.isFinite, (1...3600).contains(duration), hz.isFinite, (30...240).contains(hz) else {
            throw VirtuaTennisError.message("Seconds must be 1...3600; main-loop callback rate must be 30...240 Hz.")
        }
        let fm = FileManager.default
        let output = URL(fileURLWithPath: outputPath, isDirectory: true).standardizedFileURL
        guard !fm.fileExists(atPath: output.path) else { throw VirtuaTennisError.message("Output already exists; choose a new directory.") }
        let routeData = try Data(contentsOf: URL(fileURLWithPath: routePath))
        let replay = try JSONDecoder().decode(VTReplay.self, from: routeData)
        try replay.validate()
        let executable = URL(fileURLWithPath: CommandLine.arguments[0]).standardizedFileURL
        let manifestURL = executable.deletingLastPathComponent().appendingPathComponent("manifest.json")
        let manifestData = try Data(contentsOf: manifestURL)
        guard let manifest = try JSONSerialization.jsonObject(with: manifestData) as? [String: Any],
              let expectedExecutable = manifest["executableSHA256"] as? String,
              let expectedEngine = manifest["engineSHA256"] as? String else {
            throw VirtuaTennisError.message("Build manifest is absent or invalid.")
        }
        let engine = executable.deletingLastPathComponent().appendingPathComponent("libvirtua_tennis.dylib")
        guard digest(try Data(contentsOf: executable)) == expectedExecutable,
              digest(try Data(contentsOf: engine)) == expectedEngine else {
            throw VirtuaTennisError.message("The harness or linked shipping engine differs from its build manifest.")
        }
        try fm.createDirectory(at: output, withIntermediateDirectories: true)
        let saves = output.appendingPathComponent("fresh-saves", isDirectory: true)
        try fm.createDirectory(at: saves, withIntermediateDirectories: false)
        // VTMedia consumes these existing, supported overrides. Never open the
        // user's normal saves or write preferences during this lab run.
        setenv("VIRTUA_TENNIS_ASSET_DIR", assetPath, 1)
        setenv("VIRTUA_TENNIS_SAVE_DIR", saves.path, 1)
        let app = NSApplication.shared
        app.setActivationPolicy(.prohibited)
        app.finishLaunching()
        let scheduling = try PlaybackScheduling(mode: activityMode)
        defer { scheduling.finish() }
        let initializationStart = ProcessInfo.processInfo.systemUptime
        let scene = try VTScene(muted: false)
        scene.savesPreferences = false
        scene.diagnosticReplay = replay
        var failure: String?
        scene.onStatus = { failure = $0 }
        scene.setInputActive(true)
        scene.setPaused(false)
        let initializationSeconds = ProcessInfo.processInfo.systemUptime - initializationStart
        let start = ProcessInfo.processInfo.systemUptime
        var finished = false
        var measurements: [[String: Any]] = []
        var nextSample: Double = 5
        var displayCallbackWallSeconds: Double = 0
        var maximumDisplayCallbackSeconds: Double = 0
        var lastUpdateStart: Double?
        var maximumDisplayCallbackGap: Double = 0
        let timer = Timer(timeInterval: 1 / hz, repeats: true) { timer in
            autoreleasepool {
                let now = ProcessInfo.processInfo.systemUptime
                if let previous = lastUpdateStart { maximumDisplayCallbackGap = max(maximumDisplayCallbackGap, now - previous) }
                lastUpdateStart = now
                scene.update(now) // The scene samples one monotonic clock for display and continuation work.
                let ended = ProcessInfo.processInfo.systemUptime
                displayCallbackWallSeconds += ended - now
                maximumDisplayCallbackSeconds = max(maximumDisplayCallbackSeconds, ended - now)
                if ended - start >= nextSample {
                    var sample = scene.diagnostics()
                    sample["wallSeconds"] = ended - start
                    measurements.append(sample)
                    nextSample = ended - start + 5
                }
                if ended - start >= duration || failure != nil {
                    timer.invalidate()
                    finished = true
                }
            }
        }
        RunLoop.main.add(timer, forMode: .common)
        while !finished { RunLoop.main.run(until: Date().addingTimeInterval(0.1)) }
        let wallSeconds = ProcessInfo.processInfo.systemUptime - start
        var result = scene.diagnostics() // before shutdown flushes the real audio queue
        let state = result["gameState"] as? [String: Any] ?? [:]
        let emulated = state["emulatedSeconds"] as? Double ?? 0
        let fault = (state["faultCode"] as? NSNumber)?.uint32Value ?? UInt32.max
        var captureError: String?
        do { try scene.capture(to: output.appendingPathComponent("final.png")) }
        catch { captureError = error.localizedDescription }
        scene.shutdown()
        let schedulingRestored = scheduling.finish()
        let finalExecutableHash = digest(try Data(contentsOf: executable))
        let finalEngineHash = digest(try Data(contentsOf: engine))
        let unchanged = finalExecutableHash == expectedExecutable && finalEngineHash == expectedEngine
        let checks: [String: Bool] = [
            "durationCompleted": wallSeconds >= duration,
            "diagnosticSchedulingRestored": schedulingRestored,
            "fixedArtifactsUnchanged": unchanged,
            "mainThreadUpdates": result["updatesOnMainThread"] as? Bool == true,
            "nativeFaultFree": fault == 0 && failure == nil,
            "notPaused": result["pausedByHost"] as? Bool == false,
            "audioEngineRunning": result["isRunning"] as? Bool == true,
            "audioPlayerRunning": result["isPlaying"] as? Bool == true,
            "audioRendered": ((result["renderedFrames"] as? NSNumber)?.int64Value ?? 0) > 0,
            "noAudioEngineFailures": (result["engineFailureCount"] as? Int ?? -1) == 0,
            "noAudioUnderruns": (result["underrunCount"] as? Int ?? -1) == 0,
            "noAudioBacklogRecovery": (result["backlogRecoveryCount"] as? Int ?? -1) == 0,
            "noDiscardedClockGaps": (result["discardedClockGaps"] as? Int ?? -1) == 0,
            "emulatedTimeWithinTwoPercentOfWallTime": abs(emulated / wallSeconds - 1) <= 0.02
        ]
        result["passed"] = checks.values.allSatisfy { $0 }
        result["checks"] = checks
        result["scope"] = "Offscreen main-run-loop VTScene/VTFrameClock/VTGame/VTAudioOutput producer and real AVAudioEngine pacing; no visible window, display presentation, physical controller or audible-speaker proof."
        result["visibleWindowCreated"] = false
        result["applicationWindows"] = app.windows.count
        result["measuredWallSeconds"] = wallSeconds
        result["requestedWallSeconds"] = duration
        result["initializationSecondsExcluded"] = initializationSeconds
        result["emulatedToWallTimeRatio"] = emulated / wallSeconds
        result["timerFrequencyHz"] = hz
        result["updateWallSeconds"] = result["sceneUpdateWallSeconds"]
        result["maximumUpdateSeconds"] = result["maximumSceneUpdateSeconds"]
        result["maximumCallbackGapSeconds"] = result["maximumSceneUpdateGapSeconds"]
        result["displayCallbackWallSeconds"] = displayCallbackWallSeconds
        result["maximumDisplayCallbackSeconds"] = maximumDisplayCallbackSeconds
        result["maximumDisplayCallbackGapSeconds"] = maximumDisplayCallbackGap
        result["timingMetricsScope"] = "updateWallSeconds, maximumUpdateSeconds and maximumCallbackGapSeconds include all scene callbacks, including continuations; displayCallback fields measure only the laboratory display timer. No per-event trace is installed."
        result["routeSHA256"] = digest(routeData)
        result["routeFrames"] = replay.frames
        result["postRouteInput"] = "Neutral input after the last authored step, using VTReplay.input unchanged."
        result["freshSaveDirectory"] = "fresh-saves"
        result["buildManifestSHA256"] = digest(manifestData)
        result["executableSHA256"] = expectedExecutable
        result["engineSHA256"] = expectedEngine
        result["measurements"] = measurements
        result["scheduling"] = scheduling.report
        if let failure { result["failure"] = failure }
        if let captureError { result["captureError"] = captureError }
        let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys, .prettyPrinted])
        try data.write(to: output.appendingPathComponent("report.json"), options: .atomic)
        print(String(decoding: data, as: UTF8.self))
        if result["passed"] as? Bool != true { exit(2) }
    }
}
