# Fixed AICA ARM7 sound CPU

`scripts/compile_arm7.py` binds each admitted sound-RAM address and expected
instruction word to an offline constant-opcode operation. The generated scheduler
retains the pinned Flycast sample/cycle boundary, FIQ entry, register banks,
status flags, original memory rotation and AICA register access functions. An
unknown address or changed, unadmitted word raises the shared fixed-CPU fault.
There is no original interpreter or dynamic translator fallback.

An 8,192-slot PC index selects the one or two authenticated variants at each
aligned address in the compiled 32 KiB range. The actual instruction word must
match before an operation runs. The index changes lookup cost only; it preserves
the ordered binding identities and the original fault diagnostics, including
reads at unbound aligned sound-RAM addresses. `scripts/verify_arm7_dispatch.py`
checks every binding, changed-word rejection, alignment and unbound RAM address
against the prior ordered search, separately from the full CPU fixtures.

The fixed envelope contains all aligned words from two immutable 32 KiB ROM
windows: USA BIOS `epr-21577h.ic27 + 0x70000` and cartridge
`mpr-22924.ic9 + 0`, each mapped to sound RAM address zero. Their full-file and
window hashes are pinned by `Configuration/arm7-programs.json`. All 5,058 executed
identities in the 200 MHz original-reference route match these windows. Generation
requires verified media; optional traces validate the envelope and cannot add
instructions. The envelope includes data words as a conservative static superset
and does not claim that every possible sound path has been exercised. Generated
program words, traces, snapshots and objects remain private ignored artifacts.

The instruction implementation comes from Flycast's pinned `arm-new.h`, originally
VisualBoyAdvance, licensed GPL-2.0-or-later. Six rotate expressions are written as
defined 32-bit rotates in the derived header: their original C++ spelling shifts
by 32 for rotation zero, which changes behavior when an opcode becomes a compiler
constant. The isolated oracle still uses the original unchanged expression and
checks the resulting ARM64 behavior.

`scripts/verify_arm7.py` compares product objects against the original instruction
body with all registers, mode/interrupt state, cycle counts, memory-mapped bus
transactions and complete 8 MiB sound RAM. Whole-core original/native replays are
required separately to validate scheduling and real game sound behavior.
