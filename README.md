# Virtua Tennis fixed-engine source companion

This is a source-only development companion for an Apple Silicon port of the original NAOMI cartridge version of Virtua Tennis. It contains offline translators, the native bridge and Swift frontend, input-only laboratory tools, media identities, build scripts and validation reports. It contains no game or BIOS media, translated game-program tables, app artwork, binaries or saves.

The upstream hardware and graphics source is Flycast commit `628bd3dbb160ea2750230fc6b00c0bb8173cb1f6`. The build fetches that pinned open-source revision and selected submodules. SH-4, ARM7 and AICA DSP instructions are specialized offline from separately supplied verified media. Runtime program identity guards select fixed operations and stop on unsupported or changed code. Original CPU interpreters are restricted to development reference targets; they are not a fallback in the intended shipping engine.

The corrected reference uses the SH-4's 200 MHz clock by changing the original non-strict interpreter cycle multiplier from eight to one. Both original and fixed targets retain the same remaining hardware options for comparison. The macOS host uses actual scheduler ticks to pace rendering and audio. Early reports explicitly marked `ratio8` are historical exploratory measurements, not final timing or release qualification.

The corrected-clock fixed/reference comparisons pass 26,948 steps across singles, two-player play and accepted continuation after a match loss. Graphics, audio samples/counts, inputs and hardware ticks match exactly. The actual packaged Swift app separately passes the 6,244-step baseline. This establishes bounded path coverage, not whole-game or physical-board equivalence.

Live-audio testing has recorded brief underruns and timing recovery in a short gameplay section despite averaging approximately real time. The strict smooth-playback check is separate from replay correctness. See `Documentation/validation.md`, `Documentation/native-performance.json` and `Documentation/playback-investigation.json` for exact artifacts, measurements and limits. Physical controller actuation and audible speaker quality remain unverified.

## Rebuild requirements

Use Apple Silicon macOS 14 or later, Python 3.11 or later, and full Xcode with the macOS SDK. The build obtains pinned CMake/Ninja tools in an isolated local environment. You must supply the original files matching `Configuration/media.json`; hashes identify the accepted inputs but do not include their contents.

```sh
python3 scripts/import_assets.py --media-root "/path/to/your/local/media"
python3 scripts/prepare_native.py --jobs 6
```

All media archives, generated CPU programs, reference libraries and native binaries remain under ignored `build` paths. The reference laboratory is built independently from the fixed engine. Input traces are private generated evidence and are not distributed as program tables. See `Documentation/build.md` and the translated-CPU READMEs for the exact pipeline and measured coverage limits.

The frontend sources can be typechecked and their input/timing tests run without an engine:

```sh
scripts/build_host.sh --typecheck
scripts/build_host.sh --self-test
```

Full app packaging additionally requires local icon artwork in the format consumed by `scripts/build_icon.sh`; the private app's artwork is intentionally absent from this companion. This source payload is not a ready-to-play app. Package, gameplay, physical controller and live audio acceptance must each be established separately. The publication tool writes a local source-boundary report establishing only the reviewed inventory and bounded content scan.

## Source boundary and licenses

`Configuration/publication-files.json` is an explicit source allowlist. `scripts/prepare_publication.py` stages it locally and scans every staged file against the complete verified media and reconstructed SH-4, ARM7 and DSP images in raw/hex/base64 forms. It also rejects unlisted files, symlinks, common credential patterns and local user-directory strings. This is a bounded review aid, not a general copyright or secret classifier. The script neither invokes Git nor publishes anything.

Flycast and local engine additions use the terms described in `COPYING` and `Licenses`; component notices remain applicable. Preserved notices include upstream components that may not be linked into the final reduced build. Source licenses do not grant rights to the original game, BIOS, Sega trademarks or excluded artwork. Obtain and use the required media separately.
