// SPDX-License-Identifier: GPL-2.0-only
#include "vt_fixed_fault.h"
#include "types.h"
#include "hw/sh4/sh4_sched.h"
#include <cstdio>
#include <mutex>
#include <string>
namespace {
std::mutex error_mutex;
std::string first_error;
}
extern "C" [[noreturn]] void vt_fixed_fault(const char *cpu, uint32_t pc, uint32_t word, const char *reason) {
    char text[512];
    std::snprintf(text, sizeof(text), "Untranslated or modified Virtua Tennis %s code at %08x (word %08x): %s",
                  cpu ? cpu : "CPU", pc, word, reason ? reason : "fixed instruction identity rejected");
    std::string message;
    {
        std::lock_guard<std::mutex> lock(error_mutex);
        if (first_error.empty()) first_error = text;
        message = first_error;
    }
    throw FlycastException(message);
}
extern "C" const char *vt_fixed_error(void) {
    thread_local std::string snapshot;
    std::lock_guard<std::mutex> lock(error_mutex);
    snapshot = first_error;
    return snapshot.c_str();
}
extern "C" void vt_fixed_clear_error(void) {
    std::lock_guard<std::mutex> lock(error_mutex);
    first_error.clear();
}
extern "C" uint64_t vt_fixed_ticks(void) { return sh4_sched_now64(); }
#ifdef VT_FIXED_NATIVE
extern "C" uint32_t vt_fixed_engine_marker(void) { return 0x56544658; }
#endif
