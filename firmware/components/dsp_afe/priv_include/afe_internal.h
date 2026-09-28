#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define AFE_ALIGN_BYTES 16 // S3 SIMD kernels load 16 bytes at a time, as in dsp_spec

typedef struct {
    uint8_t *base; // NULL while only sizing
    size_t used;
    size_t cap;
} afe_arena_t;

static inline size_t afe_round_up(size_t bytes)
{
    return (bytes + AFE_ALIGN_BYTES - 1) & ~(size_t)(AFE_ALIGN_BYTES - 1);
}

static inline size_t afe_region_bytes(size_t struct_bytes)
{
    return AFE_ALIGN_BYTES + afe_round_up(struct_bytes);
}

static inline afe_arena_t afe_arena(void *mem, size_t bytes)
{
    afe_arena_t a = {0};
    if (mem == NULL) { return a; }
    const uintptr_t start = (uintptr_t)mem;
    const size_t skip = afe_round_up(start) - start;
    a.base = (uint8_t *)mem + skip;
    a.cap = bytes > skip ? bytes - skip : 0;
    return a;
}

// Returns NULL while sizing or once the region is exhausted; every block starts 16-byte aligned.
static inline void *afe_take(afe_arena_t *a, size_t bytes)
{
    const size_t at = a->used;
    a->used += afe_round_up(bytes);
    return a->base != NULL && a->used <= a->cap ? a->base + at : NULL;
}

static inline void *afe_state(void *mem, size_t bytes, size_t struct_bytes)
{
    afe_arena_t a = afe_arena(mem, bytes);
    return afe_take(&a, struct_bytes);
}

#define AFE_PI 3.14159265358979323846
#define AFE_EXP_SERIES_TERMS 40
#define AFE_TRIG_SERIES_TERMS 30
#define AFE_RECIP_MAGIC 0x7EF311C3u
#define AFE_RSQRT_MAGIC 0x5F3759DFu
#define AFE_NEWTON_STEPS 3

// Tables built at init take these double series of basic arithmetic, so every libm gives the Python mirror's
// bits.
static inline double afe_exp_series(double x)
{
    double term = 1.0;
    double total = 1.0;
    for (int k = 1; k <= AFE_EXP_SERIES_TERMS; k++) {
        term = term * x / k;
        total = total + term;
    }
    return total;
}

static inline double afe_cos_series(double x)
{
    double term = 1.0;
    double total = 1.0;
    for (int k = 1; k <= AFE_TRIG_SERIES_TERMS; k++) {
        term = term * -x * x / ((2 * k - 1) * (2 * k));
        total = total + term;
    }
    return total;
}

static inline double afe_sin_series(double x)
{
    double term = x;
    double total = x;
    for (int k = 1; k <= AFE_TRIG_SERIES_TERMS; k++) {
        term = term * -x * x / ((2 * k) * (2 * k + 1));
        total = total + term;
    }
    return total;
}

typedef union {
    float f;
    uint32_t u;
} afe_float_bits_t;

// A guess off the bits and Newton steps for positive normal floats, as srpipe's ns_omlsa: a division costs
// ~60 cycles.
static inline float afe_recip_f32(float b)
{
    float r = ((afe_float_bits_t){.u = AFE_RECIP_MAGIC - ((afe_float_bits_t){.f = b}).u}).f;
    for (int i = 0; i < AFE_NEWTON_STEPS; i++) {
        r = r * (2.0f - b * r);
    }
    return r;
}

static inline float afe_rsqrt_f32(float v)
{
    const float half = 0.5f * v;
    float r = ((afe_float_bits_t){.u = AFE_RSQRT_MAGIC - (((afe_float_bits_t){.f = v}).u >> 1)}).f;
    for (int i = 0; i < AFE_NEWTON_STEPS; i++) {
        r = r * (1.5f - half * r * r);
    }
    return r;
}
