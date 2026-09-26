/** Reader for the .gold container that ml/src/srpipe/golden/gold.py writes (KEHOACH 4.2).
 *  Works on a buffer already in memory; tensors point into it, nothing is copied or allocated.
 *  @ctx any | non-blocking | the buffer must outlive every gold_tensor_t taken from it
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#define GOLD_NAME_BYTES 32
#define GOLD_MAX_DIMS 4
#define GOLD_HEADER_BYTES 12
#define GOLD_RECORD_BYTES (GOLD_NAME_BYTES + 4 * (3 + GOLD_MAX_DIMS))

typedef enum {
    GOLD_F32 = 0,
    GOLD_I8 = 1,
    GOLD_I32 = 2,
    GOLD_U8 = 3,
    GOLD_I16 = 4,
} gold_dtype_t;

typedef struct {
    char name[GOLD_NAME_BYTES];
    gold_dtype_t dtype;
    uint32_t ndim;
    uint32_t dims[GOLD_MAX_DIMS];
    uint32_t nbytes;
    const void *data; // inside the caller's buffer, 4-byte aligned
} gold_tensor_t;

typedef struct {
    const uint8_t *buf;
    size_t len;
    size_t offset;
    uint32_t remaining;
} gold_reader_t;

/** Little-endian u32 at p, independent of the host's byte order.
 *  @ctx any | non-blocking
 */
static inline uint32_t gold_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

/** Check the header and prepare to walk the tensors.
 *  @ctx any | non-blocking
 *  @ret false for a short buffer, a wrong magic or an unknown version
 */
static inline bool gold_open(gold_reader_t *r, const void *buf, size_t len)
{
    const uint8_t *p = (const uint8_t *)buf;
    if (len < GOLD_HEADER_BYTES || memcmp(p, "GOLD", 4) != 0 || gold_u32(p + 4) != 1) { return false; }
    r->buf = p;
    r->len = len;
    r->offset = GOLD_HEADER_BYTES;
    r->remaining = gold_u32(p + 8);
    return true;
}

/** Take the next tensor.
 *  @ctx any | non-blocking
 *  @ret false when none is left or a record runs past the buffer
 */
static inline bool gold_next(gold_reader_t *r, gold_tensor_t *out)
{
    if (r->remaining == 0 || r->offset + GOLD_RECORD_BYTES > r->len) { return false; }
    const uint8_t *p = r->buf + r->offset;
    memcpy(out->name, p, GOLD_NAME_BYTES);
    out->name[GOLD_NAME_BYTES - 1] = '\0';
    out->dtype = (gold_dtype_t)gold_u32(p + GOLD_NAME_BYTES);
    out->ndim = gold_u32(p + GOLD_NAME_BYTES + 4);
    for (int i = 0; i < GOLD_MAX_DIMS; i++) {
        out->dims[i] = gold_u32(p + GOLD_NAME_BYTES + 8 + 4 * i);
    }
    out->nbytes = gold_u32(p + GOLD_NAME_BYTES + 8 + 4 * GOLD_MAX_DIMS);
    const size_t data_at = r->offset + GOLD_RECORD_BYTES;
    const size_t padded = (out->nbytes + 3u) & ~(size_t)3u;
    if (out->ndim > GOLD_MAX_DIMS || data_at + padded > r->len) { return false; }
    out->data = r->buf + data_at;
    r->offset = data_at + padded;
    r->remaining--;
    return true;
}
