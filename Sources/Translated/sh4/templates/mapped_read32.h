#pragma once
#include "hw/sh4/sh4_mem.h"
#include "hw/mem/addrspace.h"
#if !defined(__aarch64__)
#error This original mapping specialization is pinned to AArch64.
#endif
namespace addrspace { extern __attribute__((visibility("hidden"))) void* memInfo_ptr[0x100]; }
// Same original plain mapped reader, including its live mask. Handler entries
// and all alternate readers retain the original captured function-pointer call.
__attribute__((always_inline)) static inline u32 vt_mapped_read32(u32 addr)
{
    auto *const reader=ReadMem32;
    if (reader==addrspace::read32) {
        const uintptr_t entry=reinterpret_cast<uintptr_t>(addrspace::memInfo_ptr[addr>>24]);
        const uintptr_t pointer=entry & ~uintptr_t(0x1f);
        if (pointer!=0) {
            const u32 shift=static_cast<u32>(entry)&31u;
            const u32 offset=(addr<<shift)>>shift;
            return *reinterpret_cast<u32*>(reinterpret_cast<u8*>(pointer)+offset);
        }
    }
    return reader(addr);
}
