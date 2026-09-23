# Follow-up audio investigation

The delivered app and all runtime sources are unchanged. Two recovery changes
were tested and rejected because neither consistently improved the recorded
slowdown cases. The existing smooth-playback limitation remains; this work does
not replace or relax the original acceptance criteria.

A fresh four-minute producer trace completed 13,782 native steps and recorded
five audio queue shortages, one host flush and one backlog flush. Its event
sequence, generated/submitted sample counts and schedule accounting were
checked independently. Two short native-work bursts took 1.404 seconds for
1.154 emulated seconds and 1.137 seconds for 0.970 emulated seconds. Those are
real producer delays. Long uninterrupted player epochs showed approximately
one second of rendering per wall second, without evidence of meaningful
long-term audio-clock drift.

The recovery policy can amplify a shortage. The five negative queue snapshots
were only 14–163 source frames (0.32–3.70 milliseconds), but stopping the player
and accumulating its normal 40-millisecond prebuffer took 29–53 milliseconds
before the next `play()` call. Those observations alone do **not** measure the
total missing sound: timestamps are sampled in render quanta, and downstream
device latency is separate. Apple's [player-node documentation](https://developer.apple.com/documentation/avfaudio/avaudioplayernode)
also specifies that stopping unschedules the queued buffers and resets the
player timeline.

## Controlled recovery experiments

A separate real-device test fed generated stereo samples encoding consecutive
frame IDs, with the mixer muted. A bounded tap observed only this test's player
output. It checked the first and last IDs, ordering, duplication, omitted IDs
and interior zero runs. No microphone, game ROM, other application's audio or
user preferences were involved.

The unchanged player turned one deliberately induced 10.54-millisecond sampled
shortage into an 85.31-millisecond interior zero interval. All submitted IDs
were retained in order. Another baseline test crossed the backlog cap with
11,089 queued frames: stopping omitted those IDs and inserted a 74.76-millisecond
zero interval. This confirms two different recovery effects: starvation can
produce added silence, while backlog recovery can discard valid queued sound.

The compared policies were:

- **Original:** re-prime to 40 milliseconds after an underrun.
- **Immediate:** play the newly submitted nonempty buffer immediately after an
  underrun reset; other startup and flush behavior is unchanged.
- **Isolated only:** use immediate recovery only when no underrun occurred in
  the preceding second; subsequent shortages use the original prebuffer.

Each policy received identical tagged samples and frozen arrival recipes.
The burst recipes use arrival intervals from the two observed slow regions,
with a fixed synthetic warmup; they do not reproduce the original queue state
or host-clock flush. These are one-trial diagnostic comparisons, not acoustic
or general performance qualification.

| Recipe / policy | Interior gaps | Longest gap | Total gap | Submitted IDs retained |
|---|---:|---:|---:|---|
| Single miss / original | 1 | 85.31 ms | 85.31 ms | All |
| Single miss / immediate | 1 | 53.27 ms | 53.27 ms | All |
| Single miss / isolated only | 1 | 53.27 ms | 53.27 ms | All |
| First burst / original | 5 | 83.45 ms | 385.87 ms | All |
| First burst / immediate | 10 | 46.35 ms | 357.07 ms | All |
| First burst / isolated only | 5 | 86.26 ms | 352.02 ms | All |
| Continued burst / original | 3 | 76.26 ms | 228.25 ms | All |
| Continued burst / immediate | 6 | 42.81 ms | 221.20 ms | All |
| Continued burst / isolated only | 5 | 78.80 ms | 318.73 ms | 11,255 omitted by backlog recovery |

Immediate recovery shortened the isolated gap but doubled the number of gaps
during both sustained bursts. The guarded version triggered an additional
backlog reset and lost tagged frames in the continuation case. Neither is a
safe promotion based on these results. The original prebuffer, queue cap,
clock policy and sample scheduling are retained.

## Native profiling and limits

Separate sampled runs covered both problem regions. CPU stack samples remained
concentrated in fixed SH-4 execution and rendering. They did not establish a
new, narrowly scoped native optimization with a demonstrated benefit. In
particular, the ratio of time in a guarded loop helper to its enclosing chain
is not a rejection rate: the chain has several independent entry points.
No CPU instructions, scheduler budgets, hardware polling, generated audio,
compiler semantics or rendering behavior were changed.

The app, signed executable and native engine were rechecked against the
delivered artifact identities. No new packaged-app or whole-game qualification
is claimed. The player's tapped PCM is not a speaker recording, and this
investigation cannot identify the exact isolated event in the user's session.

Evidence and reusable laboratory tools:

- [Producer investigation and artifact identities](audio-hitch-investigation.json)
- [Original and rejected recovery candidates](audio-recovery-candidates.json)
- [Native sampling findings](audio-hitch-profiles.json)
- [Producer-event analyzer](../Tools/ReferenceLab/analyze_audio_trace.py)
- [Tagged audio laboratory](../Tools/ReferenceLab/TaggedAudio.md)
- [Original playback acceptance and limits](native-performance.json)

Raw traces, generated PCM, test saves, native programs and binaries remain
local under the ignored build directory. Only source tools and bounded reports
belong in the source repositories.
