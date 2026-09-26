/** Every layout that lives in flash, and only here (KEHOACH 6.2, 6.3; CLAUDE.md 1.3).
 *  Changing one bumps its version and KEHOACH 6.2 or 6.3 in the same commit.
 */
#pragma once

#include <stdint.h>

#include "gen_grid.h"

#define STORAGE_NVS_NAME_MAX_BYTES 16 // NVS_KEY_NAME_MAX_SIZE, terminator included

#define STORAGE_NS_WIFI "wifi"
#define STORAGE_NS_DEVICE "device"
#define STORAGE_NS_CALIB "calib"
#define STORAGE_NS_AFE "afe"
#define STORAGE_NS_KWS "kws"
#define STORAGE_NS_MODEL "model"
#define STORAGE_NS_SYS "sys"

#define STORAGE_KEY_SSID "ssid"                       // str
#define STORAGE_KEY_PASS "pass"                       // str
#define STORAGE_KEY_SERIAL "serial"                   // str
#define STORAGE_KEY_MQTT_URI "mqtt_uri"               // str, scheme included
#define STORAGE_KEY_MQTT_USER "mqtt_user"             // str
#define STORAGE_KEY_MQTT_PASS "mqtt_pass"             // str
#define STORAGE_KEY_STREAM_HOST "stream_host"         // str
#define STORAGE_KEY_STREAM_PORT "stream_port"         // u16
#define STORAGE_KEY_SNTP_HOST "sntp_host"             // str
#define STORAGE_KEY_TZ "tz"                           // str, POSIX TZ
#define STORAGE_KEY_BAL "bal"                         // blob STORAGE_CALIB_BAL_BYTES
#define STORAGE_KEY_BAL_VER "bal_ver"                 // u32, STORAGE_CALIB_BAL_VERSION
#define STORAGE_KEY_BAL_AT "bal_at"                   // u32, unix seconds
#define STORAGE_KEY_AEC_DELAY "aec_delay"             // u32, samples
#define STORAGE_KEY_PCM_SHIFT "pcm_shift"             // u8, bits
#define STORAGE_KEY_NS_FLOOR_DB "ns_floor_db"         // i8
#define STORAGE_KEY_AGC_TARGET_DBFS "agc_target_dbfs" // i8
#define STORAGE_KEY_VAD_MODE "vad_mode"               // u8
#define STORAGE_KEY_WAKE_TH "wake_th"                 // u16, permille
#define STORAGE_KEY_CMD_REJECT "cmd_reject"           // u16
#define STORAGE_KEY_CMD_MARGIN "cmd_margin"           // u16
#define STORAGE_KEY_ACTIVE_SLOT "active_slot"         // u8, 0 or 1
#define STORAGE_KEY_MODEL_VERSION "version"           // str
#define STORAGE_KEY_MODEL_SHA256 "sha256"             // blob 32 B
#define STORAGE_KEY_BOOT_COUNT "boot_count"           // u32
#define STORAGE_KEY_SEED_VER "seed_ver"               // u32
#define STORAGE_KEY_LAST_OTA_RESULT "last_ota_result" // u8
#define STORAGE_KEY_FW_VALID "fw_valid"               // u8

#define STORAGE_NVS_NAME_FITS(name) (sizeof(name) <= STORAGE_NVS_NAME_MAX_BYTES)
_Static_assert(STORAGE_NVS_NAME_FITS(STORAGE_KEY_AGC_TARGET_DBFS) &&
                   STORAGE_NVS_NAME_FITS(STORAGE_KEY_LAST_OTA_RESULT) &&
                   STORAGE_NVS_NAME_FITS(STORAGE_KEY_STREAM_HOST) &&
                   STORAGE_NVS_NAME_FITS(STORAGE_KEY_STREAM_PORT) &&
                   STORAGE_NVS_NAME_FITS(STORAGE_KEY_NS_FLOOR_DB) &&
                   STORAGE_NVS_NAME_FITS(STORAGE_KEY_ACTIVE_SLOT) &&
                   STORAGE_NVS_NAME_FITS(STORAGE_KEY_BOOT_COUNT) &&
                   STORAGE_NVS_NAME_FITS(STORAGE_KEY_CMD_REJECT) &&
                   STORAGE_NVS_NAME_FITS(STORAGE_KEY_CMD_MARGIN),
               "an NVS name holds at most 15 characters");

#define STORAGE_CALIB_BAL_VERSION 1
#define STORAGE_CALIB_BAL_BYTES (GEN_GRID_N_BINS * 2 * (int)sizeof(float)) // re, im per bin
#define STORAGE_MODEL_SHA256_BYTES 32

#define STORAGE_LFS_MOUNT "/lfs"
#define STORAGE_LFS_LABEL "storage"
#define STORAGE_PATH_COMMANDS "/lfs/cmd/set.json"
#define STORAGE_PATH_RESPONSES "/lfs/resp/vi.json"

#define STORAGE_MODEL_MAGIC 0x444d5253u // "SRMD" little-endian
#define STORAGE_MODEL_FORMAT_VER 1
#define STORAGE_MODEL_MAX_ENTRIES 8
#define STORAGE_MODEL_NAME_BYTES 16
#define STORAGE_MODEL_HEADER_BYTES 1024
#define STORAGE_MODEL_ALIGN_BYTES 64
#define STORAGE_MODEL_LABEL_SLOT0 "models_0"
#define STORAGE_MODEL_LABEL_SLOT1 "models_1"

typedef enum {
    STORAGE_MODEL_KIND_ESPDL = 1,
    STORAGE_MODEL_KIND_NORM = 2,
    STORAGE_MODEL_KIND_UNITS = 3,
} storage_model_kind_t;

typedef struct {
    char name[STORAGE_MODEL_NAME_BYTES];
    uint32_t offset; // from the partition start, 64-byte aligned
    uint32_t size;
    uint8_t sha256[STORAGE_MODEL_SHA256_BYTES];
    uint32_t kind; // storage_model_kind_t
    uint32_t flags;
} storage_model_entry_t;

typedef struct {
    uint32_t magic;
    uint32_t format_ver;
    uint32_t count;
    uint32_t grid_hash; // GEN_GRID_HASH of the grid the models learned on
    uint8_t reserved[48];
    storage_model_entry_t entry[STORAGE_MODEL_MAX_ENTRIES];
    uint8_t pad[STORAGE_MODEL_HEADER_BYTES - 64 - STORAGE_MODEL_MAX_ENTRIES * 64];
} storage_model_header_t;

_Static_assert(sizeof(storage_model_entry_t) == 64, "model entry is 64 bytes (KEHOACH 6.3)");
_Static_assert(sizeof(storage_model_header_t) == STORAGE_MODEL_HEADER_BYTES,
               "model header is 1 KB (KEHOACH 6.3)");
