# OpenGL reference laboratory

`GLHarness.mm` is a development harness for an explicitly selected original or instrumented libretro core. It is excluded from the shipping app. It does not translate CPUs, rewrite ROMs, inject gameplay state, or claim that the loaded engine is fixed native code.

Build against the pinned, locally staged libretro header:

```sh
python3 Tools/ReferenceLab/build_gl_harness.py
```

The build runs a real accelerated CGL/FBO/readback test and 16 player/coin-isolation checks without loading an engine. It also checks the core-profile VAO-zero rule behind the upstream GL wrapper's pending `GL_INVALID_OPERATION` diagnostic.

Run a reference replay with fresh directories (existing nonempty output/save directories are rejected):

```sh
DYLD_INSERT_LIBRARIES="$PWD/build/reference-lab/fixed-clock.dylib" \
VT_TEST_EPOCH=946684800 TZ=UTC \
build/reference-lab/gl-harness \
  --core build/reference/flycast_libretro.dylib \
  --content build/assets/vtennis.zip \
  --system build/assets/system \
  --saves build/reference-lab/example-saves \
  --out build/reference-lab/example \
  --frames 2400 --capture-every 600
```

The clock interposer is laboratory-only and must not be packaged in the app. Trace environment variables such as `VT_SH4_TRACE`, `VT_ARM7_TRACE` and `VT_AICADSP_TRACE` pass unchanged to a separately instrumented observer core. The original source checkout stays unchanged.

Optional `--route path.json` accepts `{"steps":[{"frames":3000,"buttons":0},{"frames":2,"buttons":65536}]}`. Bits 0–7 are player 1 Up/Down/Left/Right/Shot/Lob/reserved/Start; player 2 uses bits 8–15. Coins use bits 16 and 17. Unknown bits fail before engine loading. `--frames` may truncate a route but cannot extend it. Default with no route is 1,200 neutral calls.

Inputs follow the original NAOMI libretro mapping: Shot→JOYPAD_B, Lob→JOYPAD_A, Coin→SELECT, Start→START. The two ports are independent. Renderer options are recorded in each report: native 640×480, USA/English, original BIOS, DSP enabled, no freeplay or widescreen cheats, no frame skipping, unthreaded rendering. The per-content VMU option directs any initial VMU files into the supplied save directory. The system directory must contain `dc/naomi.zip` and is used by the original core for its `dc/data` directory; use a generated per-run system directory when strict write isolation is required.

Outputs are `frames.jsonl` (every presented/duplicate buffer and PCM hash/count), `report.json` (identities, options, renderer, timing and whole-PCM digest), `final.rgba`, `final.png`, and optional periodic PNGs. Raw RGBA alpha is retained for exact comparison; PNG display ignores alpha because the arcade framebuffer is an opaque screen. Unsupported optional libretro environment commands and pending upstream GL errors are recorded. Readback GL failures are fatal.

Render-to-texture VRAM readback is enabled by default, matching the app and
restoring the intro's monochrome effects. The laboratory-only option
`--rtt-readback disabled` reproduces the former flat-grey scenes;
`--rtt-readback enabled` explicitly selects the corrected setting. Other
values fail before core loading. See
[intro rendering](../../Documentation/intro-rendering.md) for the A/B checks.

A harness step is one `retro_run`, which may advance through multiple vblanks while the BIOS does not draw. It is not necessarily one 60-Hz frame. The optional `vt_fixed_ticks` read-only export adds actual SH-4 scheduler ticks to each record. Use these ticks, or PCM duration with its limits, when assessing realtime speed. The measured calls-per-second number alone is not a claim of original-speed gameplay.

The first input-only investigation found coin at call indices 3000–3001 and Start at 3060–3061 reach the player selection screen. Shot confirms the highlighted player once selection is ready. Exact accepted routes belong in the authored verification inputs after original/observer parity; the exploratory files under `build/reference-lab` are not final acceptance evidence.
