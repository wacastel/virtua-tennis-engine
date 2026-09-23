# Offscreen real-time playback laboratory

This developer-only harness compiles the shipping `VTScene`, `VTFrameClock`, `VTGame`, `VTAudioOutput`, continuation/presentation policies, controls, input and media sources unchanged. It excludes the app entry point and input-test entry point. A main-run-loop timer supplies display callbacks. The scene samples one monotonic clock for those callbacks and its own bounded continuation timers; the existing frame clock decides when to advance the fixed engine. The real `AVAudioEngine` output remains enabled. There is no visible window or substitute playback clock.

Build without running:

```sh
Tools/ReferenceLab/build_playback_harness.sh
```

After other engine builds/replays are idle, measure two minutes:

```sh
build/playback-harness/playback-harness \
  --assets build/assets \
  --route Configuration/replays/singles-baseline.json \
  --out build/verification/offscreen-playback-120 \
  --seconds 120 --hz 60 --activity default
```

The output directory must not exist. The harness creates fresh saves inside it and never reads normal application saves or writes application preferences. The authored route uses the existing presentation-indexed `VTReplay`; after its final step the existing replay implementation supplies neutral input. The callback rate controls display opportunities, not emulated speed. The production scene can also advance one pending native step from its coalesced one-shot timer between display callbacks. No live RAM or game-state overrides are used.

The build helper writes `build/playback-harness`. Preserve any executable, engine and manifest referenced by earlier reports before rebuilding that directory. Scheduling comparisons must use the same newly built executable and exact engine, with separate fresh output/save directories; comparing differently built artifacts would confound the result.

## Scheduling comparison

`--activity default` is also the behavior when the option is omitted. It makes no scheduling-policy change. `--activity user-initiated` requests main-thread `QOS_CLASS_USER_INITIATED` with the pthread API and starts a bounded `ProcessInfo` activity using `.userInitiatedAllowingIdleSystemSleep`. It changes neither the game clock nor inputs, thresholds, display mode or system-wide sleep settings.

The report's `scheduling` object records `pthread_get_qos_class_np` and `Thread.current.qualityOfService` before the request, after it and after restoration. These are requested QoS values and Foundation properties, not measurements of effective scheduling priority or CPU-core placement. The started thread's Foundation property is read rather than assigned. User-initiated mode refuses an unreadable or unspecified original pthread QoS so it cannot make a change it cannot restore. Completion ends the activity and restores the original class and relative priority; restoration is included in the pass criteria.

For example, after building and reserving an uncontended test interval:

```sh
build/playback-harness/playback-harness \
  --assets build/assets --route Configuration/replays/singles-baseline.json \
  --out build/verification/playback-default --seconds 120 --activity default
build/playback-harness/playback-harness \
  --assets build/assets --route Configuration/replays/singles-baseline.json \
  --out build/verification/playback-user-initiated --seconds 120 --activity user-initiated
```

Do not run these concurrently. A scheduling change is a diagnostic comparison, not a substitute for fixing a slow engine or evidence that the real app's foreground behavior is identical.

`report.json` binds the executable, shipping engine, build manifest and route by SHA-256. It contains five-second telemetry, final audio queue state before shutdown, elapsed wall/emulated time, callback timings and explicit checks. `final.png` captures the last engine picture after the measured interval. Initialization, final capture, shutdown and artifact rechecking are excluded from the measured playback interval. A failed check returns exit status 2 while preserving the report. The default criterion requires emulated time within two percent of wall time, no clock-gap discards, no audio underrun/backlog recovery or engine failure, and an active rendered audio timeline.

Timing fields `updateWallSeconds`, `maximumUpdateSeconds` and `maximumCallbackGapSeconds` include every production scene callback, including continuations. Separate `displayCallbackWallSeconds`, `maximumDisplayCallbackSeconds` and `maximumDisplayCallbackGapSeconds` measure only the laboratory display timer. Scene diagnostics also report display/continuation counts and texture presentations. No per-event trace is installed in the normal laboratory harness.

This measures offscreen production, real audio-device rendering and host pacing. It does **not** prove visible SpriteKit presentation, physical controller operation, audible speaker output or that a locked/occluded GUI would receive display callbacks. Report those separately. The harness and generated output are excluded from the shipping app.
