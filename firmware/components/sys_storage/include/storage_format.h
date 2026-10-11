/** Every layout that lives in flash, and only here (KEHOACH 6.2, 6.3; CLAUDE.md 1.3).
 *  Changing one bumps its version and KEHOACH 6.2 or 6.3 in the same commit.
 */
#pragma once

#include <stdint.h>

#include "app_events.h"
#include "gen_grid.h"

#define STORAGE_NVS_NAME_MAX_BYTES 16 // NVS_KEY_NAME_MAX_SIZE, terminator included

#define STORAGE_NS_WIFI "wifi"
#define STORAGE_NS_DEVICE "device"
#define STORAGE_NS_CALIB "calib"
#define STORAGE_NS_AFE "afe"
#define STORAGE_NS_KWS "kws"
#define STORAGE_NS_MODEL "model"
#define STORAGE_NS_SYS "sys"
#define STORAGE_NS_SPEAKER "speaker"

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
#define STORAGE_KEY_CMD_SYLLABLE "cmd_syllable"       // u16
#define STORAGE_KEY_CMD_SEEDS "cmd_seeds"             // blob of 3 u16: the triple last seeded
#define STORAGE_KEY_MODEL_VERSION "version"           // str
#define STORAGE_KEY_MODEL_SHA256 "sha256"             // blob 32 B
#define STORAGE_KEY_BOOT_COUNT "boot_count"           // u32
#define STORAGE_KEY_SEED_VER "seed_ver"               // u32
#define STORAGE_KEY_LAST_OTA_RESULT "last_ota_result" // u8
#define STORAGE_KEY_FW_VALID "fw_valid"               // u8
#define STORAGE_KEY_SPK_CHIP "chip"                   // u8, chip path's speaker check on
#define STORAGE_KEY_SPK_SERVER "server"               // u8, server path's on
#define STORAGE_KEY_SPK_TPL "tpl"                     // blob, the one-person layout: erased at boot
#define STORAGE_KEY_SPK_TPL_MODEL "tpl_model"         // u32, the one-person layout: erased at boot
#define STORAGE_KEY_SPK_ROSTER "roster"               // blob storage_speaker_roster_t
#define STORAGE_KEY_SPK_TPL_PREFIX "tpl"              // + slot digit: blob, float32 a dim, unit length

#define STORAGE_NVS_NAME_FITS(name) (sizeof(name) <= STORAGE_NVS_NAME_MAX_BYTES)
_Static_assert(
    STORAGE_NVS_NAME_FITS(STORAGE_KEY_AGC_TARGET_DBFS) &&
        STORAGE_NVS_NAME_FITS(STORAGE_KEY_LAST_OTA_RESULT) &&
        STORAGE_NVS_NAME_FITS(STORAGE_KEY_STREAM_HOST) && STORAGE_NVS_NAME_FITS(STORAGE_KEY_STREAM_PORT) &&
        STORAGE_NVS_NAME_FITS(STORAGE_KEY_NS_FLOOR_DB) && STORAGE_NVS_NAME_FITS(STORAGE_KEY_BOOT_COUNT) &&
        STORAGE_NVS_NAME_FITS(STORAGE_KEY_CMD_REJECT) && STORAGE_NVS_NAME_FITS(STORAGE_KEY_CMD_MARGIN) &&
        STORAGE_NVS_NAME_FITS(STORAGE_KEY_CMD_SYLLABLE) && STORAGE_NVS_NAME_FITS(STORAGE_KEY_CMD_SEEDS) &&
        STORAGE_NVS_NAME_FITS(STORAGE_KEY_SPK_TPL_MODEL),
    "an NVS name holds at most 15 characters");

#define STORAGE_CALIB_BAL_VERSION 1
#define STORAGE_CALIB_BAL_BYTES (GEN_GRID_N_BINS * 2 * (int)sizeof(float)) // re, im per bin
#define STORAGE_MODEL_SHA256_BYTES 32

#define STORAGE_SPEAKER_ROSTER_FORMAT 1

typedef struct {
    char id[APP_SPEAKER_ID_BYTES];     // spk_NNN; "" while the slot is free
    char name[APP_SPEAKER_NAME_BYTES]; // UTF-8
    uint8_t reserved[3];
    uint32_t model_id; // speaker entry's id at enrolment; 0 without a voiceprint
} storage_speaker_slot_t;

typedef struct {
    uint32_t format;  // STORAGE_SPEAKER_ROSTER_FORMAT
    uint32_t version; // of the speaker_set it holds; 0 without one
    storage_speaker_slot_t slot[APP_SPEAKERS_MAX];
} storage_speaker_roster_t;

_Static_assert(APP_SPEAKERS_MAX <= 10, "a slot is one digit of its tpl key (KEHOACH 6.2)");
_Static_assert(sizeof(storage_speaker_slot_t) == 140, "speaker slot is 140 bytes (KEHOACH 6.2)");
_Static_assert(sizeof(storage_speaker_roster_t) == 8 + APP_SPEAKERS_MAX * 140,
               "speaker roster is 1128 bytes (KEHOACH 6.2)");

#define STORAGE_LFS_MOUNT "/lfs"
#define STORAGE_LFS_LABEL "storage"
#define STORAGE_PATH_COMMANDS "/lfs/cmd/set.json"
#define STORAGE_PATH_RESPONSES "/lfs/resp/vi.json"

#define STORAGE_MODEL_MAGIC 0x444d5253u // "SRMD" little-endian
#define STORAGE_MODEL_FORMAT_VER 6
#define STORAGE_MODEL_MAX_ENTRIES 8
#define STORAGE_MODEL_NAME_BYTES 16
#define STORAGE_MODEL_HEADER_BYTES 1024
#define STORAGE_MODEL_ALIGN_BYTES 64
#define STORAGE_MODEL_LABEL_SLOT0 "models_0"
#define STORAGE_MODEL_LABEL_SLOT1 "models_1" // test_apps/partitions_unit.csv only

typedef enum {
    STORAGE_MODEL_KIND_ESPDL = 1,
    STORAGE_MODEL_KIND_NORM = 2,
    STORAGE_MODEL_KIND_UNITS = 3,
} storage_model_kind_t;

#define STORAGE_MODEL_FLAG_IN_FLASH 1u // read in place through the mapping, never copied to PSRAM

typedef struct {
    char name[STORAGE_MODEL_NAME_BYTES];
    uint32_t offset; // from the partition start, 64-byte aligned
    uint32_t size;
    uint8_t sha256[STORAGE_MODEL_SHA256_BYTES];
    uint32_t kind;  // storage_model_kind_t
    uint32_t flags; // STORAGE_MODEL_FLAG_*
} storage_model_entry_t;

typedef struct {
    uint32_t magic;
    uint32_t format_ver;
    uint32_t count;
    uint32_t grid_hash;             // GEN_GRID_HASH of the grid the models learned on
    uint32_t listen_hash;           // GEN_LISTEN_HASH the command learned on; 0 without one
    uint16_t cmd_reject_permille;   // delta1 chosen for the command; 0 without one
    uint16_t cmd_margin_permille;   // delta2 chosen for the command
    uint16_t cmd_syllable_permille; // delta3 chosen for the command; 0 without one
    uint16_t reserved0;
    uint32_t speaker_hash;                         // GEN_SPEAKER_HASH of its speaker; 0 without one
    int16_t spk_accept_permille[APP_SPEAKERS_MAX]; // with 1 + index voiceprints held
    uint8_t reserved[16];
    storage_model_entry_t entry[STORAGE_MODEL_MAX_ENTRIES];
    uint8_t pad[STORAGE_MODEL_HEADER_BYTES - 64 - STORAGE_MODEL_MAX_ENTRIES * 64];
} storage_model_header_t;

_Static_assert(sizeof(storage_model_entry_t) == 64, "model entry is 64 bytes (KEHOACH 6.3)");
_Static_assert(sizeof(storage_model_header_t) == STORAGE_MODEL_HEADER_BYTES,
               "model header is 1 KB (KEHOACH 6.3)");
