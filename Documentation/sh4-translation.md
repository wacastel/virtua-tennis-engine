# SH4 translation and validation

The source baseline is Flycast commit
`628bd3dbb160ea2750230fc6b00c0bb8173cb1f6`. Exact source identities are recorded
in `Configuration/sh4-source.json`; exact supplied media identities are in
`Configuration/media.json`. Original source, reference interpreters, derived
observations, generated program maps and objects are isolated under `build/`.

The supplied EPR boot header describes the normal 4 MiB load at `0x0c020000`
and a separate 1 MiB service load at the same destination. The normal entry is
`0x0c021000`; the service entry is `0x0c020000`. The compiler covers every
halfword-aligned position of both loads and the exact 2 MiB BIOS. This is a
static translation boundary, not a claim that every position contains an
instruction or has been executed. Undefined words retain the original SH4
illegal-instruction exception semantics.

Eight further images follow the original BIOS copy routines. Their source
offsets, loader PCs and immutable boundaries are pinned in
`Configuration/sh4-bios-copies.json`:

| BIOS source | Physical RAM destination | Translated bytes |
| --- | --- | ---: |
| `0xE0` | `0x0c0000e0` | 32, reversed halfwords |
| `0x100` | `0x0c000100` | `0x1fff00` |
| `0x800` | `0x0c001000` | `0x2800` |
| `0x60000` | `0x0c018000` | `0x7000` |
| `0x1474` | `0x0c000620` | 12 |
| `0x1494` | `0x0c000100`, `0x0c000400`, `0x0c000600` | 20 each |

The last four are immutable instruction prefixes of original 32-byte vector
copies. Their following literal pointers are patched by the original code and
are excluded from those instruction images. Ordinary data access still reads
the live RAM values. Original loader execution and exact sampled RAM bytes
are bound in `Documentation/sh4-image-acceptance.json`.

The observer records the already fetched instruction word and PC
without performing additional bus reads. Direct RAM-page snapshots provide
research provenance; the fetched word remains authoritative when instruction
cache contents differ from backing memory. The initial upstream-timing
6,244-call trace has
140,756 distinct PC/word records, 279 page snapshots and 7,320 PCs with two
observed words. The corrected-clock trace contains 143,454 executed identities;
every one matches the eleven ROM-derived images. Traces are validation inputs
only, and a clean build requires no trace or captured RAM. These observations
do not establish arbitrary self-modifying-code support: only authenticated
compiled variants are accepted.

At runtime, an address map selects a fixed function ID and expected word.
The fetched word is used only to authenticate a compiled variant. It cannot
select an arbitrary instruction handler. Original PC-relative semantics use
the live virtual PC, and cached, uncached and physical image aliases preserve
the original instruction fetch. Unknown entries fail closed through a sticky
error and controlled exception. No interpreter or JIT fallback is supplied.

Integer and FPU bodies are mechanically specialized with constant opcode
template parameters. Register/immediate extraction and branch displacements
therefore resolve during compilation. Original floating-point mode bits,
data accesses, exception paths, instruction pairing and interrupt state remain
live. The original cycle table and FPU classification are resolved offline;
the shipping code does not consult `OpPtr` or `OpDesc`.

`scripts/verify_sh4_metadata.py` compares all 65,536 instruction words with
the separately linked original opcode tables. It checks original handler
pointer identity, FPU-disabled classification, issue cycles, execution unit
and memory-cycle classification. `scripts/verify_sh4_operations.py` compares
16,953 context-local instruction words using eight prepared register/FPU
contexts and two consecutive executions, totaling 135,624 cases. It compares
the complete prepared context and architectural exception result, detects a
deliberately corrupted register result, and checks controlled failures for an
unknown PC and a changed BIOS reset instruction. Durable reports bind the
actual scripts and binaries.

These local fixtures do not independently validate every bus access, cache,
MMU transition, interrupt or delay slot. Complete original/native replays are
required in addition, and their measured coverage must be reported separately.
The source baseline's non-STRICT interpreter uses an intentional eightfold
CPU cycle multiplier. `scripts/build_reference_clock.py` preserves that
pristine source/library and builds a separate original-CPU reference with
exactly one changed header constant: `Sh4Interpreter::CPU_RATIO` becomes one.
The fixed lifecycle uses the same constant. The SH4 scheduler remains at
200 MHz, STRICT_MODE stays disabled, and all other hardware and instruction
semantics stay unchanged. Current instruction fixtures use multiplier one;
initial multiplier-eight reports and harnesses are retained as historical
evidence under ignored `build/historical/sh4-ratio8/`. Full-speed gameplay and
real-time performance still require their own measured acceptance.
