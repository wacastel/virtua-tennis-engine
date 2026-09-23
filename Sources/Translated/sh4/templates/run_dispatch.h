#pragma once
#include "vt_sh4_fixed.h"

struct VTGameTarget { uint32_t word; int32_t relative; };
extern "C" const VTGameTarget vt_game_targets[];
extern "C" const VTGameTarget vt_bios_targets[];
__attribute__((always_inline)) static inline void vt_run_execute(Sh4Context* ctx, uint16_t op, Sh4Cycles& cycles) {
    const uint32_t pc=ctx->pc-2, alias=pc & 0xe0000000u;
    const uint32_t offset=(pc & 0x1fffffff) - 0x0c020000u;
    if ((alias==0 || alias==0x80000000u || alias==0xa0000000u) && !(offset&1) && offset<0x400000u) {
        const VTGameTarget entry=vt_game_targets[offset>>1];
        if (entry.word == op) {
            const auto function=reinterpret_cast<VTFixedOperation>(reinterpret_cast<uintptr_t>(vt_game_targets)+entry.relative);
            function(ctx,cycles);return;
        }
    }
    const uint32_t physical=pc & 0x1fffffffu;
    const uint32_t ramOffset=physical - 0x0c000000u;
    if ((alias==0 || alias==0x80000000u || alias==0xa0000000u) && !(physical&1)) {
        // The RAM image is exactly ROM[0x100:]; exclude patched vectors below it.
        const bool rom=physical<0x200000u;
        if (rom || (ramOffset>=0x100u && ramOffset<0x200000u)) {
            const VTGameTarget entry=vt_bios_targets[(rom?physical:ramOffset)>>1];
            if(entry.word==op) {
                const auto function=reinterpret_cast<VTFixedOperation>(reinterpret_cast<uintptr_t>(vt_bios_targets)+entry.relative);
                function(ctx,cycles);return;
            }
        }
    }
    vt_sh4_execute(ctx,op,cycles);
}
