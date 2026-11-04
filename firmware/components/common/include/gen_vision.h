// GENERATED FILE - DO NOT EDIT.
// Source: contracts/vision.yaml
// Regenerate: python3 tools/gen_contracts.py

#pragma once

#include <stddef.h>
#include <stdint.h>

#define GEN_VISION_COMMAND_PHOTO "chup_anh"
#define GEN_VISION_COMMAND_DETECT "nhan_dien"
#define GEN_VISION_WIDTH 1280
#define GEN_VISION_HEIGHT 720
#define GEN_VISION_FRAME_SIZE FRAMESIZE_HD
#define GEN_VISION_JPEG_QUALITY 6
#define GEN_VISION_XCLK_HZ 20000000
#define GEN_VISION_SETTLE_FRAMES_MIN 4
#define GEN_VISION_SETTLE_FRAMES_MAX 30
#define GEN_VISION_STILL_FRAMES 3
#define GEN_VISION_FOCUS_TIMEOUT_MS 3000
#define GEN_VISION_PREVIEW_HOLD_MS 5000
#define GEN_VISION_VFLIP 1
#define GEN_VISION_TURN_DEG 90
#define GEN_VISION_DECODE_SCALE 2
#define GEN_VISION_BOXES_MAX 32
#define GEN_VISION_CHUNK_MAGIC 0x4d495253u // "SRIM" little-endian
#define GEN_VISION_CHUNK_VERSION 1
#define GEN_VISION_CHUNK_HEADER_BYTES 28
#define GEN_VISION_CHUNK_PAYLOAD_BYTES 16384
#define GEN_VISION_CHUNK_FLAG_PREVIEW 1

typedef struct {
    uint32_t magic;
    uint16_t version;
    uint16_t index;
    uint16_t count;
    uint16_t flags;
    uint32_t boot;
    uint32_t seq;
    uint32_t total_bytes;
    uint32_t crc32;
} gen_vision_chunk_header_t;

_Static_assert(sizeof(gen_vision_chunk_header_t) == GEN_VISION_CHUNK_HEADER_BYTES, "chunk header size");
_Static_assert(offsetof(gen_vision_chunk_header_t, magic) == 0, "magic offset");
_Static_assert(offsetof(gen_vision_chunk_header_t, version) == 4, "version offset");
_Static_assert(offsetof(gen_vision_chunk_header_t, index) == 6, "index offset");
_Static_assert(offsetof(gen_vision_chunk_header_t, count) == 8, "count offset");
_Static_assert(offsetof(gen_vision_chunk_header_t, flags) == 10, "flags offset");
_Static_assert(offsetof(gen_vision_chunk_header_t, boot) == 12, "boot offset");
_Static_assert(offsetof(gen_vision_chunk_header_t, seq) == 16, "seq offset");
_Static_assert(offsetof(gen_vision_chunk_header_t, total_bytes) == 20, "total_bytes offset");
_Static_assert(offsetof(gen_vision_chunk_header_t, crc32) == 24, "crc32 offset");
