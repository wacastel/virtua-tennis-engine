// SPDX-License-Identifier: GPL-2.0-only
// Generated archive identities; no original game bytes.
#pragma once
#include <stddef.h>
#include <stdint.h>
struct vt_media_entry { const char *path; uint64_t bytes; const char *sha256; };
static constexpr vt_media_entry vt_media[] = {
    {"vtennis.zip", 98567502ULL, "6c02a7827bd33a0bd73782de1fa11839f80096f8e12b616cc13916d001f6a3b2"},
    {"system/dc/naomi.zip", 2097280ULL, "520a5e0ef9cc86a655d31b053c3bf2a125517d9c986c4efdc0b5b32c4fb114ad"},
};
static constexpr size_t vt_media_count = sizeof(vt_media) / sizeof(vt_media[0]);
