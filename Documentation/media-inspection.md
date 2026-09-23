# Virtua Tennis media inspection

The supplied **cartridge set `vtennis` is complete** for the pinned original
MAME ROM declarations when selecting the original USA BIOS `bios14`.
It is the narrower reference candidate: 1999 *Virtua Tennis / Power Smash*,
cartridge 840-0015 / security device 317-0263-COM, machine `naomim2`.
No new media is required for this candidate. This is an inventory result;
no reference boot, playable match, fixed native engine or physical-board
validation was performed during this inspection.

The source pin is MAME `ae30b778caef8d7f9fdd45da7ac88fa42596427e`.
All eight inspected source files matched the prior project's verified original
source inventory; that project and every supplied media file remained unchanged.
The complete relative-path file inventory, sizes, CRC32, SHA-1, SHA-256,
declaration matches and archive-member metadata are in
[media-candidates.json](../Configuration/media-candidates.json).

## Cartridge and USA BIOS

The twelve cartridge files match every original size, CRC32 and SHA-1:
`epr-22927.ic22` is 4 MiB; `mpr-22916.ic1` through `mpr-22926.ic11`
are eleven 8 MiB files. The original board region is 96 MiB, including the
unpopulated 4 MiB gap at `0x00400000–0x007fffff`. The original M2/3 board
uses its existing encryption/decompression hardware with key `2803eb15`;
there is no separately required cartridge-key dump.

`naomi/epr-21577h.ic27` is an unmodified 2 MiB USA BIOS: CRC32 `fdf17452`,
SHA-1 `5f3e4b677f0046ce690a4f096b0481e5dd8bb6e6`, SHA-256
`db1b5d7f4d4c67abf67df93f517ee81b6c1ff72d86eb2f68a380c3f37ea56729`.
The original driver selector is `vtennis -bios bios14`; the default `bios0`
is Japanese. USA revisions G, E, D and A are also supplied and match their
original declarations. There is no need to select the supplied multi-region
hack or modify BIOS bytes.

The full selected cartridge configuration has **22 required verified files**:
twelve game files, the selected USA BIOS, `315-6188.ic31`, two default EEPROM
images, MIE firmware `315-6146.bin`, and all five declared JVS firmware files.
The JVS source loads the five 16 KiB firmware files into the same region in
sequence; `315-6215.bin` is the final active image. All five remain required by
the unmodified ROM loader. The supplied `vtennis`, `vtennis 2` and merged
`vtennis (1)` folders contain matching cartridge revisions; the names do not
indicate three different game versions.

## GD-ROM candidate

The separate 2001 set is `vtennisg`, *Virtua Tennis / Power Smash (GDS-0011)*,
using `naomigd`. Its USA BIOS selector is **`bios6`**, despite using the same
`epr-21577h.ic27` physical BIOS as the cartridge configuration.
The supplied 16 KiB `317-0312-com.pic` matches the original game PIC declaration.

`gds-0011.chd` is CHD v5, 46,751,544 physical bytes and 1,344,333,888 logical
bytes. `chdman 0.281 verify` successfully checked both raw-data and overall
SHA-1. Its content SHA-1 `5ae669832805139f973dc86ab7cab66aa8166ac0`
matches the driver's disc identity. The copy inside `vtennisg-chd.zip` is
byte-identical. Thus the disc and game PIC are present, but **the complete
current DIMM configuration is not**.

The pinned `naomigd.cpp` additionally declares these absent files:

| Required device file | Bytes | CRC32 | SHA-1 |
| --- | ---: | --- | --- |
| `315-6301.ic11` | 130,907 | `cc7735c7` | `1afb442b5918c0d60f98688ed0a7117b0d068722` |
| `317-unknown.pic` | 16,384 | `7dc07733` | `b223dc44718fa71e7b420c3b44ce4ab961445461` |
| `dimmspd.bin` | 128 | `45dac6d7` | `4548675f8d31348fa6828d5b4f247af1f072b62d` |
| `93c46.bin` | 128 | `daafbccd` | `1e39983779a62ebc6801ec6f2a5138717a7a5259` |

`315-6301.ic11` and `317-unknown.pic` belong to the board's shared ROM
inventory, while `dimmspd.bin` is used twice for DIMM SPD EEPROMs and
`93c46.bin` initializes the board EEPROM. Their being ancillary or unused
in a particular gameplay path does not justify deleting original declarations.
The supplied default DIMM BIOS `fpr-23489c.ic14` and all supplied alternative
DIMM BIOS files match the original SHA-1 declarations. The optional alternative
`fpr23905c.ic36` is absent; it is not required when selecting the supplied
default. No missing media was downloaded or synthesized.

## Hardware and fixed-translation implications

The cartridge machine configures more than its main SH-4:

| Component | Pinned original behavior | Fixed-port implication |
| --- | --- | --- |
| Main SH7091, 200 MHz | 2 MiB BIOS, 32 MiB work RAM and aliases, cartridge DMA and M2/3 decryption | Capture and authenticate loaded program images, relocated RAM execution and actual BIOS instructions; do not treat the cartridge as one directly mapped program. |
| ARM7 sound CPU, 2.8224 MHz | Executes from the 8 MiB sound RAM shared with SH-4; AICA registers at ARM address `0x00800000` | Identify uploaded sound code and active ARM/Thumb modes independently. No separate sound program ROM is declared. |
| AICA effects DSP | Runtime-programmable 128-step MPRO, eight 16-bit words per step | Requires fixed program identities and original upload-transition handling, separate from PCM/timer hardware. |
| MIE Z80, configured 16 MHz | 2 KiB firmware, writable RAM, Maple/JVS bridge | Existing original `device_start` applies a RAM-test patch at ROM offset `0x144`; provenance must include that upstream behavior. |
| JVS TMP90PH44, configured 10 MHz | Executes its 16 KiB internal ROM; the device also implements JVS protocol in C++ | Preserve the existing device model and account for the CPU, rather than silently assuming a CPU-free input board. |
| M3COMM 68000, configured 10 MHz | No ROM; shared uploaded RAM. Held in reset by `device_reset_after_children`, releasable by host MMIO | Observe whether this game releases it. If active, its uploaded program also needs fixed translation. Do not infer inactivity merely from an absent firmware file. |

The GD-ROM machine additionally creates a DIMM SH7091 and a PIC16C622. Choosing
the complete cartridge candidate avoids those additional processors and missing
DIMM files; it does not remove any processor from the cartridge configuration.
PowerVR rendering, board timing, communication and JVS handling retain the
original model's hardware abstractions and limitations.

Both game entries inherit `MACHINE_NOT_WORKING`, `MACHINE_IMPERFECT_GRAPHICS`
and `MACHINE_IMPERFECT_SOUND` in this pin. These flags require measured original
reference boot and match evidence before treating it as a suitable gameplay
baseline; they cannot establish either success or failure on their own. Fixed
native execution must not fall back to the original interpreter if a loaded
instruction/program image is missing.

## Inspection limits and reproducibility

All 153 non-hidden supplied files were hashed. All members of the six ZIP
archives were decompressed, CRC checked and independently SHA-1/SHA-256 hashed.
The two 7z archives received container hashes only; their supplied extracted
counterparts were independently inspected. Only the two metadata documents
were created. No media, original source, live save, app or repository was
modified, and no gameplay claim follows from these checks.

Primary code references: [game/BIOS declarations and machine configuration](https://github.com/mamedev/mame/blob/ae30b778caef8d7f9fdd45da7ac88fa42596427e/src/mame/sega/naomi.cpp),
[DIMM requirements](https://github.com/mamedev/mame/blob/ae30b778caef8d7f9fdd45da7ac88fa42596427e/src/mame/sega/naomigd.cpp),
[JVS board](https://github.com/mamedev/mame/blob/ae30b778caef8d7f9fdd45da7ac88fa42596427e/src/mame/sega/jvs13551.cpp),
[MIE](https://github.com/mamedev/mame/blob/ae30b778caef8d7f9fdd45da7ac88fa42596427e/src/mame/sega/mie.cpp),
[M3COMM](https://github.com/mamedev/mame/blob/ae30b778caef8d7f9fdd45da7ac88fa42596427e/src/mame/sega/m3comm.cpp),
and [AICA DSP](https://github.com/mamedev/mame/blob/ae30b778caef8d7f9fdd45da7ac88fa42596427e/src/devices/sound/aicadsp.cpp).
