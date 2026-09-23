# Fixed AICA DSP

`scripts/compile_aicadsp.py` selects an exact complete MPRO image, then runs 128
fixed step functions whose instruction fields are compile-time literals. The
original runtime instruction decoder and instruction loop are removed from the
native link. Coefficients, input signals, ring-buffer data, accumulator timing,
memory access timing and PACK/UNPACK operations retain the original hardware
implementation. Unknown complete program images raise the shared fixed-CPU fault.

The first original boot/gameplay capture contains one complete DSP program and
its load/clear intermediates. The full program matches supplied cartridge data
at `mpr-22926.ic11 + 0x4398` (also present in two other sound-data files). Every
ascending 32-bit-slot load prefix from zero and every corresponding clear prefix
produces exactly the same 644 distinct images admitted by the generator. This
includes all 184 distinct images actually sampled during the 200 MHz original
reference route. `Configuration/aicadsp-program.json` pins the cartridge program;
generation derives its full load/clear closure without requiring traces. Optional
traces only validate that envelope and cannot add programs. A stable
program is checked by exact comparison; program changes use hash lookup followed
by complete comparison, never instruction decoding.

The implementation derives from the pinned Flycast AICA DSP hardware code and
Audio Overload SDK interpreter. Preserve their source notices and the project's
third-party licenses. ROM words, complete programs and generated tables remain
ignored local build artifacts.

`scripts/verify_aicadsp.py` tests every admitted image with four randomized
hardware states and four consecutive samples at product optimization, comparing
the complete DSP state, register block and all 8 MiB sound RAM. It also rejects
unknown images and checks the original all-zero stopped state. Whole-core
original/native audio and video comparisons are separate integration evidence.
