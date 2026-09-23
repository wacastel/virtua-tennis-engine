#pragma once
#include "mapped_fetch.h"
#include "vt_sh4_fixed.h"
#include "hw/sh4/sh4_interpreter.h"
#include "hw/sh4/sh4_mem.h"
#include "vt_integer_templates.h"
#include "vt_floating_templates.h"
__attribute__((always_inline)) static inline u16 vt_block_fetch(Sh4Context *ctx)
{
	u32 addr = ctx->pc;
	if (!mmu_enabled() && (addr & 1))
		// address error
		throw SH4ThrownException(addr, Sh4Ex_AddressErrorRead);

	ctx->pc = addr + 2;

	return vt_fetch_read16(addr);
}
