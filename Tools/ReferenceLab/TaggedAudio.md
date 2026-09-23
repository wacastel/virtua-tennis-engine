# Tagged audio recovery laboratory

These tools exercise an explicitly selected `VTAudioOutput` implementation with generated stereo sample IDs. They never link the game engine, read game media, access a microphone, change preferences, or modify the app. The mixer is muted; a player-node tap captures only these generated samples before the mixer. This is not a speaker recording or a hardware xrun measurement.

Build into a fresh ignored directory:

```sh
python3 Tools/ReferenceLab/build_tagged_audio.py \
  --audio-source Sources/Mac/AudioOutput.swift \
  --out build/audio-lab/control
```

The builder copies the selected source unchanged and records source, harness, compiler and executable identities. A candidate must be supplied as a separate file; the builder contains no permanent candidate patches. Existing output directories are rejected to preserve prior evidence.

Run a bounded case when other playback/performance jobs are idle:

```sh
build/audio-lab/control/tagged-audio-harness steady build/audio-lab/steady
build/audio-lab/control/tagged-audio-harness starvation build/audio-lab/starvation
build/audio-lab/control/tagged-audio-harness backlog build/audio-lab/backlog
```

The adaptive starvation case waits for a sampled negative pending count. Its actual timing depends on render phase. It is useful to discover behavior, but a policy comparison must freeze the original producer events and replay the identical recipe for both policies. The backlog case deliberately exercises the existing queue cap; its output may omit samples because stopping the player discards pending buffers.

`make_audio_recipes.py` accepts completed synthetic producer events and the original diagnostic `audio-batches.jsonl`. It exports the measured single miss and two selected slow-arrival intervals, with source hashes. Burst recipes add a fixed one-second warmup and omit the original host clock flush; they do not reproduce the whole game or its earlier queue history.

```sh
python3 Tools/ReferenceLab/make_audio_recipes.py \
  --trace build/audio-hitch-investigation/baseline-trace-240/audio-batches.jsonl \
  --single-miss-events build/audio-hitch-investigation/tagged-baseline/starvation/producer-events.json \
  --out build/audio-lab/recipes
build/audio-lab/control/tagged-audio-harness recipe \
  build/audio-lab/recipes/single-miss.json build/audio-lab/replayed-single-miss
```

The harness preallocates tap storage, records render timestamps, and decodes both channels into source-frame IDs after playback. Require first ID zero, last ID `submittedFrames-1`, no omissions, duplicates, invalid IDs or reordered IDs, and no capture overflow. Initial silence and the deliberate final drain are excluded from interior-gap metrics. Drain time must cover the pending queue and the actual tap callback quantum; requested tap size is not guaranteed.

`analyze_tagged_audio.py --help` describes the portable comparison specification. It verifies recipe/input/output/source/executable identities and reports total and maximum interior zero runs, gap groups, queue extrema and producer lateness. The first trial is the control. Candidate gates include complete sample conservation, no additional gap groups, no worse maximum/total interior silence, and no additional backlog flushes. A favorable single trial still needs render-phase repeats and real-game qualification.

The first two narrow recovery experiments are rejected in `Documentation/audio-recovery-candidates.json`. The production implementation and delivered app remain unchanged. Historical PCM, traces, candidate sources and binaries stay in ignored `build/` output; only the tools and measured metadata belong in source control.
