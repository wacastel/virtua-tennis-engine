# Building and validating the app

The application target is arm64 macOS 14 or later. Use Python 3.11 or later and a full Xcode installation selected by `xcode-select`; Command Line Tools alone do not supply the icon tooling. `xcrun swiftc`, `xcrun actool`, `xcrun vtool`, `codesign`, `sips` and `iconutil` must be available. The reference builder creates an isolated Python environment under `build/tooling` for pinned CMake 3.31.10 and Ninja 1.13.0. Initial dependency preparation requires network access.

## Standard build

```sh
./build.sh --media-root "/path/to/your/Virtua Tennis media" --jobs 6
```

The media directory is searched only for the filenames, sizes and hashes declared in `Configuration/media.json`, including matching ZIP members. The importer builds deterministic local cartridge and BIOS archives in `build/assets`. No original media is downloaded or modified. Later builds may reuse these verified archives; omit `--media-root` to do so. With no imported assets, the default input directory is `Virtua Tennis ROMs` inside the checkout.

The top-level entry calls `scripts/prepare_native.py --jobs N`. That engine workflow must prepare the pinned original source and corrected-clock reference, derive every required fixed CPU program from verified inputs, compile all three CPUs, generate media identities and produce `build/native/libvirtua_tennis.dylib` plus `build/native/manifest.json`. The build deliberately stops if this workflow or an audited shipping artifact is unavailable. A clean checkout must regenerate ignored CPU programs; copied scratch traces or an interpreter-only library are not a shipping substitute.

The original source and reference remain under `build/reference-source/flycast` and `build/reference`. The corrected reference uses `build/clock-source/flycast` and `build/reference-clock`, with the documented `CPU_RATIO` change from eight to one. Generated native code and its manifests live under `build/generated` and `build/native/cpu`. Runtime timing uses the 200 MHz SH-4 scheduler.

Preparation runs original/native CPU fixtures for instruction semantics, mapped fetch/read helpers, blocks, call chains, counter loops, register-only sequences, ARM7 dispatch and the DSP. After linking, it also verifies every SH-4 target-table entry and the configured native function order. These are correctness and provenance checks; live playback is measured separately.

After preparation, packaging validates the engine, CPU manifests, generated-program manifests, source/object hashes, bridge/media identities and corrected-reference binding. It stages exactly the two consumed archives, the icon, license notices and the fixed engine dylib. The Swift executable links directly to that engine. The finished bundle is signed locally and checked for arm64 architecture, deployment target and system-only dependencies. The package report is `build/package/manifest.json`.

The output is `build/Virtua Tennis.app`. `./Play.command` opens it with Launch Services. You may move the completed app without moving the source checkout; saves remain in Application Support. Quit a running copy before rebuilding it. This local ad-hoc signature is not Apple notarization.

## Packaging an existing verified engine

```sh
./build.sh --skip-engine
```

This skips engine regeneration only. All source, CPU, media and artifact identity checks still run. Changed sources, stale manifests, missing CPU objects, reference/hybrid markers, failed symbol inspection or a missing native-engine marker stop packaging. Do not use this option to bypass a failed engine qualification.

## Source-only host checks

```sh
scripts/build_host.sh --typecheck
scripts/build_host.sh --self-test
python3 Sources/Mac/Tests/verify_build_guard.py
```

These checks do not build or launch a game engine. They exercise the actual input router, controller slot behavior, short taps, timing debt and rejection of non-shipping libraries. Their reports are under `build/host-tests`. They are useful while CPU generation is in progress.

## Packaged diagnostics

The real executable lists its options with:

```sh
"build/Virtua Tennis.app/Contents/MacOS/VirtuaTennis" --help
"build/Virtua Tennis.app/Contents/MacOS/VirtuaTennis" --controllers
```

`--diagnostic-run` and `--audio-replay` consume authored digital-input routes. A route's `frames` count means completed presentation/timeout steps, not physical display vblanks. Compare actual emulated seconds, raw RGBA, all stereo PCM samples and sample counts. Diagnostic modes use temporary saves unless `--save-dir` explicitly selects another isolated directory. Visible diagnostics must not compete with a user's active game.

The separate laboratory clock interposer is only for deterministic paired tests. It is never bundled or injected into normal launch. Source-only tests, successful packaging and short boot captures are not proof of whole-game coverage, real-time performance, physical controller actuation or continuous speaker playback. The final release must retain those measured limits in its acceptance reports.
