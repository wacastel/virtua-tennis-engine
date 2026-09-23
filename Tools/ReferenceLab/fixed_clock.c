// SPDX-License-Identifier: GPL-2.0-only
// Test input only: provide a fixed host RTC seed to paired laboratory processes.
// No emulated scheduler or gameplay timer is changed. Never bundle in the app.
#include <time.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
static time_t vt_test_time(time_t *out) {
    const char *value = getenv("VT_TEST_EPOCH");
    time_t result;
    if (value && *value) {
        char *end;
        errno = 0;
        long long parsed = strtoll(value, &end, 10);
        if (errno || *end || parsed < 0) {
            fputs("Invalid VT_TEST_EPOCH\n", stderr);
            abort();
        }
        result = (time_t)parsed;
    } else {
        time_t (*original)(time_t *) = dlsym(RTLD_NEXT, "time");
        if (!original) abort();
        result = original(NULL);
    }
    if (out) *out = result;
    return result;
}
__attribute__((used,section("__DATA,__interpose")))
static const struct { const void *replacement; const void *original; } pair = {
    (const void *)vt_test_time, (const void *)time
};
