# Audio producer timing instrumentation

This optional developer helper measures the existing producer path using explicitly instrumented copies under ignored `build/performance/audio-instrumentation`. It does not edit production Swift, the bridge, or the ordinary playback harness. It preserves the existing input sequence, native calls, frame clock, 40 ms prebuffer, 250 ms queue limit and flush decisions.

Build only, without starting a game or audio device:

```sh
python3 Tools/ReferenceLab/build_audio_instrumentation.py
```

The output directory must be new. Use `--out build/performance/audio-instrumentation-new` for subsequent builds so earlier executable/engine evidence is preserved. By default, the helper copies the current verified fixed engine. `--engine` and `--engine-manifest` can select an isolated experimental engine whose manifest pins the current fixed parent, candidate sources, objects and product. Original/reference/observer/diagnostic/stub markers and original decoder symbols are rejected.

Only run during an agreed interval without competing engine work:

```sh
build/performance/audio-instrumentation/playback-harness \
  --assets build/assets --route Configuration/replays/singles-baseline.json \
  --out build/verification/audio-instrumented-120 --seconds 120 --activity default
```

The normal harness's temporary saves, argument checks, artifact checks and playback criteria remain. This helper adds a trace-completeness criterion; reaching its 65,536-event capacity stops recording metadata and makes that criterion fail. It never changes engine progress to preserve the trace. The recording is bounded in memory; JSON serialization and disk writes happen after the measured interval, capture and shutdown. No PCM samples, audio tap, microphone or unrelated application audio is recorded.

`replacements.json` contains every exact original/replacement source fragment. `manifest.json` pins the builder, original and derived sources, replacements, executable and exact engine. A changed or ambiguous source anchor fails the build. The producer records:

- `nativeReturn`: native-call start and return timestamps, return status, attempted-step index, and the **previous** emulated time.
- `gameReturn`: completion of the original Swift validation/picture/PCM copies, new emulated time and stereo batch size.
- `enqueueBefore`: arrival at `present`, the existing pre-enqueue rendered/scheduled sample counters and pending queue. Rendered/pending values are absent until the original player clock is valid.
- `enqueueAfter`: the existing post-schedule counters and final player-start state. Its render snapshot comes from the original post-schedule query, before any first `player.play()` call.
- `playerStart` and `flush`: original start/flush decisions, with exact flush reason and scheduled counter.
- `measurementStart` and `measurementEnd`: the original timed interval boundaries. Initialization and shutdown events outside those markers are retained but must not be included in playback-rate calculations.

Timestamps use `ProcessInfo.systemUptime`. Consecutive native-return and game-return records distinguish native work from Swift copies; enqueue arrival and before/after records distinguish producer delay from queue/scheduling work. Batch duration is `batchSampleFrames / sampleRate`; queue duration uses the same rate. No extra AVAudio render-time query is introduced: instrumentation reuses the existing queries.

The trace adds timestamp reads and preallocated-array appends. Its overhead has not been measured and it is not a replacement for an uninstrumented performance result. Five-second aggregate telemetry cannot locate an individual production deadline; use these events to test whether a queue drain preceded a long native call or occurred despite adequate production throughput. A larger prebuffer must not be used to conceal sustained sub-real-time execution. A real buffering change, if supported later, needs separate qualification with the original playback criteria.
