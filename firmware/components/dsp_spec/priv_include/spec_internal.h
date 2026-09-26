#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "dsp_spec/fft.h"

#define SPEC_ALIGN_BYTES 16 // S3 SIMD FFT kernels load 16 bytes at a time
#define SPEC_MIN_FFT_POINTS 64
#define SPEC_MAX_FFT_POINTS 2048

typedef struct {
    uint8_t *at;
    uint8_t *end;
} spec_carver_t;

static inline size_t spec_round_up(size_t bytes)
{
    return (bytes + SPEC_ALIGN_BYTES - 1) & ~(size_t)(SPEC_ALIGN_BYTES - 1);
}

static inline bool spec_fft_points_ok(size_t n)
{
    return n >= SPEC_MIN_FFT_POINTS && n <= SPEC_MAX_FFT_POINTS && (n & (n - 1)) == 0;
}

static inline spec_carver_t spec_carver(void *mem, size_t bytes)
{
    const uintptr_t start = (uintptr_t)mem;
    const uintptr_t aligned = (start + SPEC_ALIGN_BYTES - 1) & ~(uintptr_t)(SPEC_ALIGN_BYTES - 1);
    spec_carver_t c = {.at = (uint8_t *)aligned, .end = (uint8_t *)mem + bytes};
    return c;
}

// Returns NULL once the region is exhausted; every block starts 16-byte aligned.
static inline void *spec_carve(spec_carver_t *c, size_t bytes)
{
    uint8_t *block = c->at;
    c->at += spec_round_up(bytes);
    return c->at <= c->end ? block : NULL;
}

size_t spec_fft_points(const dsp_spec_fft_t *fft);
