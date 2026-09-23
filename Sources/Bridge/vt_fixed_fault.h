// SPDX-License-Identifier: GPL-2.0-only
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
[[noreturn]]
#else
_Noreturn
#endif
void vt_fixed_fault(const char *cpu, uint32_t pc, uint32_t word, const char *reason);
const char *vt_fixed_error(void);
void vt_fixed_clear_error(void);
uint64_t vt_fixed_ticks(void);
uint32_t vt_fixed_engine_marker(void);
#ifdef __cplusplus
}
#endif
