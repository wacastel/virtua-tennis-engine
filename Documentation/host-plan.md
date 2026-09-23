# Virtua Tennis Mac frontend plan

This is a design and reference-laboratory record, not an acceptance claim for a translated game. The shipping engine remains conditional on fixed offline SH-4, sound ARM and AICA DSP work. No gameplay cheat was requested.

## Selected baseline and available resources

The parent task selected Flycast commit `628bd3dbb160ea2750230fc6b00c0bb8173cb1f6`, now checked out under `build/reference-source/flycast`. The original checkout stays unchanged. Its interpreter-only libretro build is the development reference; it is not a shipping fallback. The canonical selected game is the 1999 `vtennis` cartridge with the verified USA BIOS. The supplied GDS-0011 disc is a different rerelease and must not be mixed into the cartridge manifest.

A bounded local inventory found Xcode, OpenEmu and NeoBox installations, but no Flycast/Reicast app or Dreamcast core among OpenEmu's installed cores. Prior Model 2 projects contain a complete pinned MAME source tree, including NAOMI research material. They remain read-only, and MAME was not selected as this game's baseline. No existing local production NAOMI renderer or fixed SH-4 engine was found in the inspected project/cache locations.

The useful reusable code is the Virtua Fighter 2 Cocoa/SpriteKit frontend: GameController assignment, two independent keyboard/controller masks, short-tap latching, focus/sleep/disconnection pause, neutral-input gating, audio queue, menu lifecycle, packaged media checks, and per-app preferences. Its Model 2 dimensions, timing, health addresses, invincibility API, gameplay bootstrap and acceptance reports are not transferable.

## Controls and identity

Sega's original-game service documentation describes a joystick, Shot and Lob, with one player against the computer or two-player singles. Its input test calls Shot SW1 and Lob SW2. The 1999 cartridge's pinned Flycast entry uses the same `shot12_inputs` two-button descriptor. This is not the sequel's topspin/slice scheme or the Dreamcast version's four-player doubles controls. [Sega Virtua Tennis kit manual, game instructions and input test](https://manualzz.com/doc/11957062/sega-naomi-virtua-golf--virtua-tennis-arcade-game-install...), [pinned cartridge/input descriptors](https://github.com/flyinghead/flycast/blob/628bd3dbb160ea2750230fc6b00c0bb8173cb1f6/core/hw/naomi/naomi_roms.cpp).

| Action | DualSense | Player 1 keyboard | Player 2 keyboard |
|---|---|---|---|
| Move / aim | D-pad or left stick, digital eight-way | Arrows | W/A/S/D |
| Shot | Cross | Z | F |
| Lob | Circle | X | G |
| Insert coin | L1 | 5 | 6 |
| Start / confirm original menu | Options | Return or 1 | 2 |
| Host pause / resume | L3 | P or Escape | Shared |

Triangle and I remain unused. No invincibility or timer-freeze menu, replay field, flag or backend hook should be inherited. Reset and sound/fullscreen remain host menu actions. Preserve stable controller slots; disconnecting player 1 must not transfer a still-connected player 2 into player 1's slot. An ignored third pad should not clear an active player's held input.

Use app `Virtua Tennis.app`, executable `VirtuaTennis`, bundle/preference domain `local.william.virtuatennis`, and a separate Application Support save directory. Inside the actual bundle use `UserDefaults.standard`; an explicit suite is only for an unbundled test process. Root supplies original icon artwork and the existing native asset-catalog packaging workflow.

## Proposed narrow C ABI

Use opaque `vt_context` and the established create/destroy/reset/error/fault conventions. These are interface requirements, not an implemented engine declaration:

```c
vt_context *vt_create(const char *asset_directory, const char *save_directory);
void vt_destroy(vt_context *);
int vt_reset(vt_context *);
int vt_step(vt_context *, uint32_t buttons);
const char *vt_error(const vt_context *); /* null context may report create failure */
uint32_t vt_fault_code(const vt_context *);
uint64_t vt_frame_number(const vt_context *); /* completed presentation steps */
double vt_frame_rate(const vt_context *);    /* current nominal video rate */
double vt_emulated_seconds(const vt_context *); /* actual scheduler time */
int vt_width(const vt_context *);
int vt_height(const vt_context *);
double vt_aspect_ratio(const vt_context *);
const uint8_t *vt_pixels(const vt_context *); /* top-down tightly packed RGBA8 */
const int16_t *vt_audio(const vt_context *);  /* interleaved signed stereo */
uint32_t vt_audio_count(const vt_context *); /* stereo frames in last step */
uint32_t vt_audio_sample_rate(const vt_context *);
```

Per-player byte: Up `1`, Down `2`, Left `4`, Right `8`, Shot `16`, Lob `32`, Start `128`; bit `64` is reserved. Player 2 uses bits 8–15. Coin 1 and coin 2 use bits 16 and 17. The complete allowed mask is `0x3bfbf`; unknown bits fail before stepping. Opposite directional pairs should neutralize consistently. Returned buffers are borrowed until the next mutating call; all engine/CGL calls are serialized on one owning thread. No asynchronous callback may change returned memory while Swift is consuming it.

Only one engine per process should be accepted until global Flycast state is explicitly isolated. Save/reset must clear input and transient audio, preserve existing records, and retain deterministic cold-state behavior. Package creation must verify media hashes before engine execution and reject reference/diagnostic library markers. A fixed-engine unsupported PC/image must yield a controlled fault and stop, never invoke an interpreter.

## Renderer and timing route

The pinned tree supplies OpenGL and Vulkan renderers, not a direct native Metal renderer. Its macOS Vulkan route uses MoltenVK. Apple's `Metal` framework in the build is not evidence of a separate Metal backend. The upstream Apple setup script can install Homebrew/change Xcode selection/download MoltenVK; it should not be run blindly. The root task instead acquired local build tooling and built the original libretro target with OpenGL on, Vulkan off and the original interpreter backend. [Pinned CMake](https://github.com/flyinghead/flycast/blob/628bd3dbb160ea2750230fc6b00c0bb8173cb1f6/CMakeLists.txt), [Apple setup script](https://github.com/flyinghead/flycast/blob/628bd3dbb160ea2750230fc6b00c0bb8173cb1f6/shell/apple/generate_xcode_project.command).

`Tools/ReferenceLab/GLHarness.mm` now loads an explicitly selected libretro dylib with an accelerated offscreen CGL core context and a complete color/depth/stencil FBO. The core receives real framebuffer/procedure callbacks, then its context-reset callback runs after content loading. Each presentation is read back to RGBA, with bottom-left orientation corrected; raw alpha is retained for comparison, while PNG presentation ignores framebuffer alpha. Every audio batch is retained, and duplicate video callbacks keep the prior picture. The original core and any instrumented observer run in separate fresh processes, with explicit isolated saves and system directories.

The selected media archive includes a BIOS, but upstream's BIOS loader can reopen the game by a relative name. To avoid a process-working-directory dependency, stage a verified BIOS-only `system/dc/naomi.zip` explicitly. The harness initially reproduced the missing-BIOS failure and then booted successfully with that canonical system layout.

The offscreen CGL/readback self-test and 16 independent player/coin-routing checks pass. A 2,400-call original-engine run reached English `INSERT COIN(S)` attract rendering at 640×480, 4:3. The queried rate was approximately 59.793771 Hz; output audio was 44,100 Hz stereo. This is bounded reference evidence, not translated-engine or physical-controller acceptance.

Crucially, one `retro_run()` is **not always one hardware video frame**. In unthreaded mode, `Emulator::vblank()` stops a run after more than 50 ms without a rendered frame. Early boot calls produced roughly 2,211 stereo sample frames each, versus roughly 737 during ordinary display. Production pacing therefore needs actual SH-4 scheduler-time deltas (preferred) or generated PCM duration as a fallback. Muting speakers must not disable engine audio generation or advance the game faster. Do not carry over Model 2's fixed frame accumulator unchanged. [Pinned emulator lifecycle](https://github.com/flyinghead/flycast/blob/628bd3dbb160ea2750230fc6b00c0bb8173cb1f6/core/emulator.cpp).

The original GL state wrapper leaves `GL_INVALID_OPERATION` pending in core-profile unbinding: it binds VAO zero before disabling vertex arrays. The harness records pre-existing errors separately and independently checks the readback operations; it does not silently call them readback failures. A focused CGL self-test reproduces that VAO-zero rule. This upstream diagnostic issue and any future render discrepancies need to remain qualified until the slim production adapter is audited.

For the first frontend, retain the same offscreen GL hardware renderer and upload its RGBA result to the existing SpriteKit view. This is simple to validate but incurs readback/upload overhead; measure it at real speed before accepting usability. A later CAMetalLayer/Vulkan/MoltenVK path would need its own source pins, packaging/dependency audit, synchronization and image qualification. It must not silently change the renderer used for accepted comparison evidence.

## Validation and remaining gates

1. Complete authentic coin/start/character-select/serve and both Shot/Lob input routes in the original engine. Inspect English title, menus and live play; record startup boundaries without RAM/PC injection.
2. Compare fresh original and observer runs with identical media, options, input, initial RTC policy, framebuffer handling and saves. Capture scheduler time, SH-4/ARM program images and AICA DSP uploads. Ensure trace code does not perform an extra guest instruction fetch.
3. Replace every executed guest CPU/DSP program with pinned offline translations. A runtime dynarec or generic interpreter is not an acceptable shipping path. Preserve hardware abstractions for JVS/Maple, PowerVR and AICA, while describing them accurately rather than claiming physical auxiliary processors were translated when they are modeled at a higher level.
4. Compare fixed vs original RAM/register state, per-step audio samples/counts, images, loaded-code guards and lifecycle across roster/menu/match/round/result/continue paths. Any RTC normalization belongs to paired laboratory runs; ordinary user saves/time must retain original behavior.
5. Test the host independently: two-controller assignments, tap latches, focus/neutral gating, pause/resume, reset and save closure; then actual packaged rendering/audio timing. Distinguish enumeration and synthetic controller tests from physical button actuation. Do not infer audible speaker continuity from a software audio queue alone.
6. Audit the final arm64/macOS 14 app itself for fixed CPU provenance, absent original decoders/JITs, allowed system dependencies, resource/media identity, license notices and signature. Prior game reports provide methods, not acceptance evidence for this game.

No prior project, user save, active game or installed application was modified by this work. The reference-only harness has reached authentic player selection/live court play. The production CGL bridge and Swift host are now implemented and syntax/typechecked, with 100 synthetic input/clock checks passing; they still await the qualified fixed engine before a product link or playable-app claim.
