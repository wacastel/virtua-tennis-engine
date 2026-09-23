// SPDX-License-Identifier: GPL-2.0-only
#pragma once
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef struct vt_context vt_context;
enum {
    VT_UP = 1, VT_DOWN = 2, VT_LEFT = 4, VT_RIGHT = 8,
    VT_SHOT = 16, VT_LOB = 32, VT_START = 128,
    VT_COIN_1 = 1 << 16, VT_COIN_2 = 1 << 17,
    VT_VALID_INPUT = 0x3bfbf
};
// Player 2 uses the same direction/action/start byte shifted left by eight.
// One context per process, serialized on its creating thread. Asset identities
// are verified before boot. Save directory must be private and writable.
vt_context *vt_create(const char *asset_directory, const char *save_directory);
void vt_destroy(vt_context *);
int vt_reset(vt_context *);
int vt_step(vt_context *, uint32_t buttons);
const char *vt_error(const vt_context *); // null reports create failure
uint32_t vt_fault_code(const vt_context *);
uint64_t vt_frame_number(const vt_context *); // completed frontend steps
double vt_frame_rate(const vt_context *); // nominal hardware display rate
double vt_emulated_seconds(const vt_context *); // actual SH-4 scheduler time
int vt_width(const vt_context *);
int vt_height(const vt_context *);
double vt_aspect_ratio(const vt_context *);
const uint8_t *vt_pixels(const vt_context *); // top-down tightly packed RGBA8
const int16_t *vt_audio(const vt_context *); // interleaved signed stereo
uint32_t vt_audio_count(const vt_context *); // stereo sample frames in last step
uint32_t vt_audio_sample_rate(const vt_context *);
// Buffers/errors are borrowed until the next mutating call. No cheat API.
#ifdef __cplusplus
}
#endif
