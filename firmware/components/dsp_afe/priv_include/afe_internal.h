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
