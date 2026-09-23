#pragma once
#include "vt_sh4_fixed.h"
// False leaves architectural CPU and RAM state unchanged.
bool vt_try_counter_loop(Sh4Context *ctx,Sh4Cycles &cycles);
