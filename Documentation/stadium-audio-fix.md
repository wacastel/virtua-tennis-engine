# Stadium crowd audio

The first stadium view after player selection briefly exceeded the host's
real-time processing budget. The crowd PCM matched the reference, but late production
drained the audio queue and triggered stop/re-prime recovery. The retained
40 ms prebuffer, 250 ms queue cap and clock-recovery policy are unchanged.

The fix enables Apple's OpenGL driver worker once when creating the context.
The fixed native CPU translations and renderer submission still execute on the creating
thread; synchronous readback completes before each native step returns. This
is separate from Flycast's threaded-rendering option, which remains disabled.
If the optional driver capability cannot be confirmed, initialization continues
with the available driver support and logs that status.

Frame presentation also uploads an opaque RGBA copy directly to SpriteKit.
Only the copy's alpha bytes are set to 255, matching the former CGImage
`noneSkipLast` interpretation. The original RGBA, captures and replay hashes
remain unchanged. Nearest filtering and top-down image orientation are retained.

## Evidence

The focused diagnostic sample placed 413 of 1,348 main-thread samples inside
OpenGL draw calls, primarily the sorted translucent color pass. The sampler
approximately covered steps 4,133–4,203; sampling perturbs execution and does
not establish clean playback performance.

In serialized instrumented trials, the same 69-step stadium interval advanced
1.154 emulated seconds. Native execution took 1.370 seconds in the baseline
and 0.980 seconds with the driver worker and direct texture upload. The latter
recorded no stadium shortage or clock gap. Two earlier queue shortages occurred
during silent startup PCM; the entire trial's strict no-underrun check therefore
still failed. A worker-only trial also improved the stadium interval but had
more startup recoveries. These diagnostic results are retained as failures,
not relabeled as whole-run smooth-playback passes.

A redundant sampler-parameter cache and a smaller output framebuffer did not
resolve the stadium shortage and were not promoted. No upstream source, CPU
object, instruction guard, emulated clock, input mapping or audio sample was
changed. The only native link input changed is the production bridge object.

The final packaged app matches the original reference across 6,244 steps:
RGBA, PCM, sample counts and integer scheduler ticks all agree, including
5,003,652 stereo sample frames. The production texture helper separately
passes four synthetic patterns through both texture readback and the same
offscreen SpriteKit view, with zero differing bytes. These checks include
varied source alpha, gray/color patterns, wrong-orientation negative controls,
invalid input rejection and raw-input preservation. The host's 159 checks and
the real engine's 63 lifecycle/boundary checks also pass.

An uninstrumented four-minute run of the final production sources recorded
four queue shortages and one discarded clock gap, all before the first
five-second measurement. The counters then remained unchanged for the rest
of the route, including the first stadium view and the stadium view after
continuing. The strict whole-run check remains **failed**; coarse measurements
cannot identify those startup frames or establish that their PCM was silent.

The rebuilt app also completed a visible 110-second replay without a native
fault. Its aggregate report recorded three shortages and one clock gap; that
report alone cannot locate the events. Visual inspection confirmed the
grayscale intro still displays image detail, and the captured gameplay image
retains the expected colors and orientation.

A separate visible diagnostic copy recorded complete per-step timing metadata
without taking screenshots during playback. It used the final engine and
unchanged scene, with reversible in-memory instrumentation. Its shortages
occurred at boot steps 8, 10 and 13, and intro step 1,251; a host clock-recovery
flush occurred at step 14. No further recovery occurred through step 6,049,
including the stadium view. The 69-step stadium interval took 0.986 seconds of
native execution for 1.154 emulated seconds. This diagnostic trial still fails
a strict whole-run no-recovery check, and cannot retrospectively locate the
events in the earlier aggregate-only visible report.

Final live-playback results and exact artifact identities are recorded in
[the acceptance report](stadium-audio-fix.json). Reference parity and bounded
queue telemetry do not establish physical arcade equivalence, speaker quality,
all controller actions or an entire tournament. Historical performance reports
remain evidence for their recorded artifacts, not the rebuilt application.

## Reproduction

Build and run the ROM-free presentation test:

```sh
python3 Tools/ReferenceLab/build_texture_presentation.py --out build/texture-check
build/texture-check/texture-presentation-harness build/texture-check/results
```

Use the existing playback harness with `singles-continue.json` for the longer
route, and the packaged app's `--audio-replay`, `--audio-report` and
`--quit-after` options for visible validation. Run performance jobs serially,
with fresh isolated save directories. Raw traces, captures, media, generated
programs and builds remain local and excluded from publication.

Apple documents the driver-worker option, its initialization-time use and
the need for measurement in its
[OpenGL concurrency guide](https://developer.apple.com/library/archive/documentation/GraphicsImaging/Conceptual/OpenGL-MacProgGuide/opengl_threading/opengl_threading.html).
