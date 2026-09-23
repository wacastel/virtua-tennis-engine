// SPDX-License-Identifier: GPL-2.0-only
// Read-only original-hardware clock probe for the isolated laboratory.
#include "hw/sh4/sh4_sched.h"
extern "C" uint64_t vt_fixed_ticks(void) { return sh4_sched_now64(); }
