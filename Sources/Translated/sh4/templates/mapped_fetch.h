#pragma once
#include "vt_sh4_fixed.h"
#include "hw/sh4/sh4_interpreter.h"
#include "hw/sh4/sh4_core.h"
#include "hw/sh4/sh4_mem.h"
#include "hw/mem/addrspace.h"
#if !defined(__aarch64__)
#error This measured original mapping specialization is pinned to AArch64.
#endif
namespace addrspace { extern __attribute__((visibility("hidden"))) void* memInfo_ptr[0x100]; }
// Keep the active reader and live mapping observable on every fetch. The pinned
// original AArch64 read16 uses a 32-bit LSRV, whose shift count is its low 5 bits.
__attribute__((always_inline)) static inline u16 vt_fetch_read16(u32 addr)
{
    auto *const reader=IReadMem16;
    if (reader==addrspace::read16) {
        const uintptr_t entry=reinterpret_cast<uintptr_t>(addrspace::memInfo_ptr[addr>>24]);
        const uintptr_t pointer=entry & ~uintptr_t(0x1f);
        if (pointer!=0) {
            const u32 shift=static_cast<u32>(entry)&31u;
            const u32 offset=(addr<<shift)>>shift;
            return *reinterpret_cast<u16*>(reinterpret_cast<u8*>(pointer)+offset);
        }
    }
    return reader(addr);
}
__attribute__((always_inline)) static inline u16 vt_mapped_fetch(Sh4Context *ctx)
{
    u32 addr=ctx->pc;
    if (!mmu_enabled() && (addr&1))
        throw SH4ThrownException(addr,Sh4Ex_AddressErrorRead);
    ctx->pc=addr+2;
    return vt_fetch_read16(addr);
}
