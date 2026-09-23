# Fixed SH-4 execution optimizations

This document describes execution and build boundaries. Artifact-bound reports record the validation results; this design description does not establish playback performance.

The build derives all admitted instructions from authenticated local media and
pinned original source. Development traces help identify expensive regions;
they do not supply instructions or become build inputs. Unknown PCs and changed
words still reach the fixed-code fault boundary. No runtime decoder or JIT is
introduced.

## Dispatch and ordinary memory access

The game and BIOS use immutable tables of expected words and relative native
function targets. The post-link verifier checks every table entry against the
generated plan and actual linked symbols. The ordinary single-instruction path
remains available for all authenticated entries.

The optimized Run path groups 256 game blocks and 128 BIOS blocks, with at most
16 instructions per block. An interior instruction requires positive remaining
cycles, the expected PC before fetch, the expected PC after fetch, and the
expected fetched word. If admission fails after a fetch, the existing fixed
single-instruction path receives that already-fetched word exactly once.
Step and ordinary delay-slot/RTE execution keep their original paths.

Two exact self-loops, five delay-slot sites and a six-node call/return chain
avoid repeated generic dispatch. Their operation bodies and cycle metadata
come from the original source. The chain preserves each original fetch, data
access, exception and cycle charge unless the separately guarded counter-loop
optimization below succeeds.

Instruction fetch and block-local 32-bit reads may use a direct load only when
the installed reader is the original plain address-space function and the live
mapping selects contiguous memory. Each access reads the current mapping.
Handlers and alternate readers use the captured original callback. Independent
fixtures compare these helpers against the actual original address-space
implementation, including remapping, boundaries, unusual alignments, side
effects and exceptions. These changes do not replace ordinary writes or other
data widths.

## Bounded counter-loop translation

One continuing counter loop can be expressed as native arithmetic. Admission
requires the exact 33 original instructions, expected call targets and literal,
plain original memory functions, actual NAOMI main-RAM mappings, stable cycle
pairing state and sufficient cycle budget. Debugging, MMU, threaded rendering,
network, rollback and multiboard observation are excluded by build or runtime
guards. All preflight loads are bounded direct RAM reads; failed admission
leaves architectural state unchanged and uses the existing chain.

Resolved host RAM ranges, including mirrored guest addresses, establish that
the four destinations do not overlap each other, code, live read dependencies,
or CPU/cycle state. Only complete continuing iterations are combined. A batch
contains at most 14 iterations and leaves a positive counter and cycle budget.
Each iteration still costs 30 original cycles and 14 memory-operation counter increments.
The four final stores and exact register/flag state reproduce that boundary.
The next instruction fetch, partial iteration, final iteration and original
448-cycle scheduler/interrupt boundary remain on the existing execution path.

Unlike the dispatch optimizations, a successful arithmetic batch intentionally
omits repeated ordinary RAM fetches, reads and overwritten stores. Its proof
boundary is complete CPU/cycle/RAM state at batch and scheduler boundaries,
under the no-observer guards; it does not claim an identical bus-event trace.
Mapped-RAM differential tests must establish actual fast-path execution,
rejection without mutation, full-state equivalence and pending-interrupt
delivery. Callback-based fixtures alone cannot validate this optimization.

## Register-only sequences

Sixteen bounded sequences can omit repeated mapped-RAM instruction reads after
checking every original instruction word in the sequence. The selected
operations modify only CPU registers and floating-point state. They do not
read or write data memory, branch or call device callbacks. Original operation
order, FPU-disabled checks, PC increments, cycle pairing and exception prefixes
remain unchanged. A changed future instruction falls back before mutation, so
the ordinary path still reaches that change at its original boundary.

Admission requires the original plain readers, a contiguous mapping to actual
main RAM, no observer/rollback/network modes, disjoint CPU/cycle storage and
sufficient remaining budget for every operation. The generator proves the
maximum cost across all six possible preceding execution units. The remaining
budget stays positive at every interior point, preserving the original
scheduler boundary. Independent fixtures call the actual retained helper
implementations and compare full CPU, cycle and 32 MiB RAM state, including
exceptions, rejected guards and pending interrupts.

## Native function placement

A configuration of original image/PC references determines the preferred
placement of 256 native functions. The generator resolves these references
against verified media and the complete fixed target plan. The linker consumes
the resulting authenticated order file, and the post-link verifier checks the
actual symbol order. Placement changes no operation bodies or scheduling.
The configuration contains no captured instruction stream or profile counts.

## Build and validation boundaries

Ordinary operations, optimized blocks, chain templates, target assembly and the
derived address-space object have explicit compiler profiles. Dependency files
verify the selected headers. Generated inputs, source helpers, configurations,
objects and link inputs are hashed and rechecked before packaging. Reference
interpreters remain isolated in laboratory targets.

CPU fixtures and complete original/native replay comparisons establish bounded
correctness evidence. Separate production-host playback tests measure pacing
and audio recovery. Neither proves unvisited gameplay paths, physical arcade
hardware equivalence, visible display presentation or physical controller use.

The `status` and `candidatePlanSHA256` fields in `Configuration/sh4-blocks.json`
are frozen metadata from the original proposal review, not current build status.
The final canonical build, fixtures and library identities are recorded in
[`cpu-acceptance.json`](cpu-acceptance.json).
