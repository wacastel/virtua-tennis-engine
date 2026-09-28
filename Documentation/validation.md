# Validation and limits

The fixed engine was built and tested on an Apple M3 Ultra with macOS 27 beta,
using an arm64 macOS 14 deployment target. The selected media is the original
NAOMI Virtua Tennis cartridge with the USA/English BIOS. This is an offline CPU
translation project; original interpreters are isolated in the reference lab.

The current engine SHA-256 is
`cb80e534e94afa6f2be4b8399bc11e2694d0a2c4b47d322b85b05dcbce4ac57c`.
Ad-hoc signing changes the packaged Mach-O hash; the package report binds that
signed copy separately.

The current Mac host maps pause/resume to DualSense Create rather than the
left-stick press. It passes 159 source checks and the Swift typecheck. The
[pause and opening-serve investigation](pause-and-serve.md) records current
host/package evidence and a 5,646-step neutral-after-selection comparison.

The [intro rendering correction](intro-rendering.md) enables rendered-texture
readback into VRAM, restoring the monochrome shots that previously appeared
flat grey. The September 24 graphics-correction build passes 6,000 original/native and packaged-app
steps through the intro, title, tutorial and player showcase, with exact
RGBA, PCM, sample counts and scheduler ticks. The A/B comparison preserves
audio and timing over 3,600 steps and changes all 11 selected monochrome
samples while preserving 15 adjacent color samples. The existing 6,244-step
singles route also passes again through the original/native pair and the
actual packaged app, and all 63 bridge lifecycle checks pass. That build's evidence is in
[intro effect acceptance](intro-render-acceptance.json) and
[current artifact validation](intro-render-validation.json).

The table below records the earlier qualification of engine
`636b4ad2edf5336cc3e6f5f9c29d10d79600a243460f77e7f505e6c333d6f50e`.
Its CPU translations and host sources were unchanged by that graphics fix;
the longer two-player/continuation routes have not been rerun with readback
enabled. Earlier reference parity also reproduced the grey-scene defect,
so it did not establish correct monochrome rendering.

| Check | Result and scope |
|---|---|
| CPU semantics and guards | Passed original/native SH-4, ARM7 and AICA DSP fixtures, including complete CPU/RAM state, mapped reads, exceptions and interrupt boundaries. |
| SH-4 blocks | 131,874 cases and 11,036 guard cases, including both authenticated BIOS mappings. |
| Linked SH-4 dispatch | All 3,145,728 game/BIOS expected-word and native-target entries passed; configured function placement was verified. |
| Baseline singles replay | All 6,244 steps and 5,003,652 stereo sample frames matched. |
| Two-player replay | All 9,004 steps and 7,044,477 stereo sample frames matched, including each player winning a game. |
| Singles continuation | All 11,700 steps and 9,032,936 stereo sample frames matched, including match loss, the original Continue countdown and accepted continuation. |
| Packaged app | Its actual Swift executable, C ABI and bundled media matched the 6,244-step reference; 63 bridge boundary/lifecycle checks passed. |
| Mac host source checks | 145 input/timing/presentation assertions, host typechecks and six invalid-build rejection checks passed. |

Replay equality includes RGBA pixels, every PCM sample, sample counts and
200 MHz scheduler ticks. The laboratory core replays also compare original
input records. Deterministic tests use fresh isolated saves and a laboratory
RTC seed; normal launches use the ordinary system clock. Concurrent correctness
runs are not performance measurements.

## Playback and visible presentation

The September 24 120-second visible attract-mode run with readback enabled advanced
6,580 steps and 119.010 emulated seconds. The rebuilt window visibly displayed
detailed monochrome imagery and recorded no engine fault, audio underrun or
clock/backlog recovery in that run. It used real audio output, the normal system
clock and fresh isolated diagnostic saves, with no competing engine runs.
This short intro check does not resolve the prior gameplay audio limitation.

The earlier four-minute offscreen test used the production host, real
AVAudioEngine output, fresh saves and the singles-continuation route. It ran
13,769 native steps, advancing 239.524 emulated seconds in 240.048 wall seconds
(0.997820 times real time), without a native fault. It recorded seven audio
underruns, two clock-gap recoveries and one backlog recovery. **The strict
smooth-playback test failed.** Overall speed and exact replay correctness do
not establish uninterrupted audio or remove this known playback limitation.

A separate 100-second run of the actual packaged app verified an unobscured
window, English menus and a player-one rally. It advanced 5,279 native steps
without a native fault. It also recorded four audio underruns and one timing
recovery, so the playback limitation is present in the visible app too. One
DualSense was detected; its physical buttons and sticks were not actuated.

A [follow-up audio investigation](audio-hitch-investigation.md) measured the
producer delays and tested two recovery changes using tagged PCM. Both changes
were rejected after regressions in the controlled burst cases. The delivered
audio implementation remains unchanged. Those measurements predate the RTT
correction; the original playback limitation remains unresolved.

## Evidence

- [CPU and build acceptance](cpu-acceptance.json)
- [Baseline original/native comparison](native-clock1-acceptance.json)
- [Extended gameplay comparisons](extended-replay-acceptance.json)
- [Actual packaged replay](packaged-replay-acceptance.json)
- [Bridge checks](bridge-acceptance.json)
- [Host checks](host-source-acceptance.json)
- [Live performance](native-performance.json)
- [Visible window and controller detection](gui-environment-limit.json)
- [Earlier performance investigation](playback-investigation.json)
- [Follow-up audio investigation](audio-hitch-investigation.json)

These tests do not establish whole-tournament coverage, every character or
unvisited gameplay path, equivalence to a physical arcade board, physical
controller-button actuation or audible speaker quality. Controller enumeration
is distinct from physical input testing. No independent rebuild of the public
source companion or testing on another Mac/macOS version is claimed.

Raw media, reconstructed programs, generated binaries, saves and screen captures
remain local and are excluded from both source repositories. The private game
repository includes the app artwork. The public companion requires separately
supplied verified media and local artwork for full app packaging.
