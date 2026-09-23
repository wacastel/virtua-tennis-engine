# Virtua Tennis frontend

Standalone arm64/macOS 14 frontend for the fixed Virtua Tennis NAOMI engine. It has no interpreter or fallback engine. `Sources/Bridge/virtua_tennis.mm` links directly to the verified fixed backend and owns an offscreen CGL context. No arbitrary core-loading UI exists.

App identity, preferences and Application Support data use `local.william.virtuatennis`. Default launch reads bundled media and separate writable saves. Diagnostic runs create temporary saves unless an explicit `--save-dir` is given. The bridge verifies the exact consumed cartridge/BIOS archives and copies the BIOS into its private writable system cache; bundled resources are never a save target.

## Controls

| Action | DualSense | Player 1 keyboard | Player 2 keyboard |
|---|---|---|---|
| Move/aim | D-pad or left stick | Arrows | WASD |
| Shot | Cross | Z | F |
| Lob | Circle | X | G |
| Coin | L1 | 5 | 6 |
| Start / original menu confirm | Options | 1 / Return | 2 |
| Pause | L3 | P / Escape | Shared |

Triangle and I are unused. No gameplay cheat was requested. The original arcade game supports singles against the computer or a second player. Shot is original SW1; Lob is SW2.

Two controllers keep their slots through unrelated connections and disconnections. An assigned disconnect, focus loss or sleep pauses the host. Held active controls must return to neutral after focus/reconnect. Short keyboard/controller taps survive display updates that produce no engine step. Opposed directions cancel independently for each player. Start while host-paused resumes without leaking an arcade Start press.

## Timing, graphics and audio

NAOMI's native renderer can return after multiple vblanks when no picture is drawn. `VTFrameClock` accumulates wall time, then subtracts the exact SH-4 scheduler-time delta from each successful call. A long boot step retains negative timing debt; it is not charged as one nominal display frame. The nominal engine frame rate is informational and queried dynamically. Both display callbacks and pending-work callbacks use the same monotonic clock. Each callback advances at most one native step and accounts for its elapsed wall time at entry and exit. Positive remaining debt schedules one cancellable common-mode timer 100 µs in the future, allowing the main run loop to handle other events. The 250 ms discontinuity and excess-debt guards remain unchanged. Pause, reset, failure and shutdown cancel pending work; pause or sleep also clears queued audio and timing debt.

The renderer returns top-down RGBA data after its first presentation. The host retains the previous picture across duplicate frames. Only external display callbacks upload a texture, using the latest completed image even when that display callback advances no native step. Reset clears the presentation marker. Every native step and PCM batch still executes, and display upload cost remains included in wall-clock accounting. It ignores framebuffer alpha for opaque on-screen/PNG presentation while preserving raw alpha in comparison data. This frontend uses offscreen GPU rendering followed by CPU readback and SpriteKit upload; real-time performance still requires actual packaged measurement.

Original stereo PCM is produced even when muted. AVAudioEngine performs device-rate conversion and reports queue/underrun telemetry. Variable audio counts are bounded before buffer access. Software queue measurements alone do not prove continuous speaker playback.

## Build and tests

```sh
scripts/build_host.sh --typecheck
scripts/build_host.sh --self-test
```

These modes typecheck the real frontend and test the actual keyboard/controller router, variable-time clock, cancellable timer queue and presentation marker without an engine or stub. Timer tests include cancellation/replacement, fully accounted debt, real common-mode timer fairness with synthetic step delays, and unchanged input order. The synthetic test uses Apple extended gamepad objects; it does not claim physical controller actuation.

Normal linking requires `build/native/libvirtua_tennis.dylib` with both the fixed-engine marker and production `vt_create` export. Missing symbols, failed inspection, and reference/observer/diagnostic/stub markers stop the build. App packaging must copy the audited dylib into `Contents/Frameworks`, retain its source/media provenance and notices, and sign the completed bundle.

The actual executable supports `--help`, `--controllers`, headless input-only replays, per-step RGBA/PCM traces, captures, bounded visible/audio runs and isolated saves. Replay `frames` means completed presentation/timeout steps, not hardware vblanks. Reports include actual emulated seconds. Deterministic paired laboratory runs may use the separate test-only RTC interposer; normal application launch must not inherit a frozen clock.

The host's self-tests are independent evidence only. Engine semantics, whole-match coverage, the packaged C ABI, original/fixed parity, source/media/link audits, real GUI and live audio require separate qualification before claiming a completed port.
