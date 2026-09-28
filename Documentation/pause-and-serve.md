# Pause mapping and opening serve

Pause/resume now uses the DualSense Create button, the small button left of
the touchpad. Pressing the left stick no longer pauses or blocks movement.
The right-side Options button remains arcade Start and can also resume a
paused session. Keyboard P/Escape and the Game menu retain their existing
pause behavior. The controls help and paused-screen hint show the new mapping.

The existing neutral-input guard and press-edge handling apply to Create.
Holding it produces one toggle; gameplay controls must be released before
resuming. Neither Create nor a resume press is sent as an arcade game input.
The host tests pass 159 checks, including 14 new synthetic-controller checks
covering both players, stick presses during movement, held Create, neutral
gating and Options/Start routing. The full Swift source typecheck also passes.
These are Apple synthetic gamepad tests, not physical button actuation.

## Opening serve investigation

The reported automatic opening serve reproduces in the original NAOMI game
code as well as the port. The controlled route inserts one coin, starts the
game, confirms Jim Courier with a two-step Shot tap, then releases every
control for the remaining 1,800 steps. The final selection tap is step 3,846;
every input record from 3,847 through 5,646 is zero.

Both the first and following serve begin despite that neutral input. Captures
show the first toss at step 4,650 and its rally, followed by the next service
position and another toss. The original-code reference and fixed native core
match at all 5,646 presentation boundaries: RGBA, PCM samples/counts, inputs
and 200 MHz scheduler ticks. The first toss occurs over 13 emulated seconds
after the last selection button, ruling out a pending host tap in this route.

This establishes that the observed idle-serve behavior comes from the arcade
game's execution, rather than an automatic input added by the Mac frontend.
It does not establish Sega's design intent, physical-board equivalence or
the Dreamcast release's timing. Serving rules and game code were not changed.

The authored input-only route is
`Configuration/replays/neutral-after-selection.json`. It uses the same
fixed laboratory RTC, fresh isolated saves and RTT-enabled graphics settings
as the other current replays. Native programs, ROMs, saves, frame streams and
captures remain under ignored local build paths. Artifact-bound results are
recorded in [pause and serve acceptance](pause-and-serve-acceptance.json).
