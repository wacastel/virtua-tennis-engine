# Intro monochrome rendering

The original intro alternates color animation with monochrome tennis shots.
The previous build displayed those monochrome shots as uniform grey, apart
from the coin prompt. The fixed native core and the original-CPU reference
both reproduced the defect, so their earlier pixel parity did not establish
correct rendering for this effect.

## Cause and correction

The pinned renderer defaults `reicast_enable_rttb` to disabled. In that mode,
`core/rend/gles/gltex.cpp`'s `ReadRTTBuffer()` keeps the rendered image in a GPU
texture cache instead of writing its pixels into guest VRAM. Enabling the
existing readback path lets the game's monochrome effect use the rendered
image. The bridge now explicitly enables this option on creation and reset.
Full framebuffer emulation remains at its original disabled setting.

This uses the existing pinned renderer without changing upstream source,
game media, generated CPU programs or runtime instruction guards. No new
interpreter or runtime translation path is introduced.

## Evidence and reproduction

The initial 3,600-step neutral-input comparison reproduced identical grey
frames in the previous native build and the original reference. Repeating
the reference with readback enabled changed 758 pictures while preserving
every PCM sample, sample count, input, dimension and scheduler tick.

Visual inspection of 11 sampled monochrome shots confirmed recognizable
players, court, crowd and shadows in place of flat grey. The stadium view
corresponds to the scene in the supplied arcade screenshot. This is a visual
comparison, not a pixel-exact physical-board test. Fifteen adjacent color
samples remain pixel-identical. The automated comparison checks those hash
changes and invariants; it does not classify image quality.

Build the laboratory harness with `Tools/ReferenceLab/build_gl_harness.py`.
It now defaults to readback enabled, matching the app; use
`--rtt-readback disabled` to reproduce the former behavior. With the fixed
laboratory RTC, fresh isolated saves, neutral inputs and captures every 60
steps, the first clear before/after example is step 1,260. Run at least 3,600
steps for the selected samples. Compare the completed runs using:

```sh
python3 scripts/verify_intro_render.py \
  --before build/intro-render-investigation/reference/capture \
  --after build/intro-render-investigation/rtt/capture \
  --output build/intro-render-investigation/intro-effect-acceptance.json
```

The lab's initial A/B runs used different harness builds solely to expose
the readback option. Their executable identities are recorded separately.
The subsequent original/native parity runs use the same final harness.

Captures, raw replay streams and binaries remain in ignored local build
directories. The checked-in acceptance summary binds their hashes. See
[validation and limits](validation.md) for the current artifact checks and
the separate existing live-audio limitation.
