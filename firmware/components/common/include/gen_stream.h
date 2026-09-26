// GENERATED FILE - DO NOT EDIT.
// Source: contracts/stream/frame.yaml
// Regenerate: python3 tools/gen_contracts.py

#pragma once

#include <stddef.h>
#include <stdint.h>

#define GEN_STREAM_MAGIC 0x54535253u     // "SRST" little-endian
#define GEN_STREAM_VERSION 1
#define GEN_STREAM_HEADER_BYTES 24

typedef enum {
    GEN_STREAM_MODE_OFF = 0,
    GEN_STREAM_MODE_CLEAN = 1,
    GEN_STREAM_MODE_RAW = 2,
    GEN_STREAM_MODE_RAW_REF = 3,
    GEN_STREAM_MODE_RAW_REF_CLEAN = 4,
    GEN_STREAM_MODE_RAW_CLEAN = 5,
} gen_stream_mode_t;

typedef enum {
    GEN_STREAM_FORMAT_S16LE = 0,
} gen_stream_format_t;

typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t mode;
    uint64_t t_us;
    uint32_t seq;
    uint8_t channels;
    uint8_t format;
    uint16_t samples;
} gen_stream_header_t;

_Static_assert(sizeof(gen_stream_header_t) == GEN_STREAM_HEADER_BYTES, "stream header size");
_Static_assert(offsetof(gen_stream_header_t, magic) == 0, "magic offset");
_Static_assert(offsetof(gen_stream_header_t, version) == 4, "version offset");
_Static_assert(offsetof(gen_stream_header_t, mode) == 6, "mode offset");
_Static_assert(offsetof(gen_stream_header_t, t_us) == 8, "t_us offset");
_Static_assert(offsetof(gen_stream_header_t, seq) == 16, "seq offset");
_Static_assert(offsetof(gen_stream_header_t, channels) == 20, "channels offset");
_Static_assert(offsetof(gen_stream_header_t, format) == 21, "format offset");
_Static_assert(offsetof(gen_stream_header_t, samples) == 22, "samples offset");

/** Channels a frame carries in this mode; 0 for an unknown mode.
 *  @ctx any | non-blocking
 */
static inline uint8_t gen_stream_mode_channels(gen_stream_mode_t mode)
{
    switch (mode) {
    case GEN_STREAM_MODE_OFF: return 0;
    case GEN_STREAM_MODE_CLEAN: return 1;
    case GEN_STREAM_MODE_RAW: return 2;
    case GEN_STREAM_MODE_RAW_REF: return 3;
    case GEN_STREAM_MODE_RAW_REF_CLEAN: return 4;
    case GEN_STREAM_MODE_RAW_CLEAN: return 3;
    default: return 0;
    }
}
