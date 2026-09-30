/** What travels over the TCP link of espsr_compare (KEHOACH 3.16): the PC sends a job and the item's input,
 * the board answers every variant with a result and, when it ran, its mono output. items.py reads every
 * constant here.
 *  @ctx any | all integers and floats little-endian, samples int16 at GEN_GRID_SAMPLE_RATE_HZ
 */
#pragma once

#include <stdint.h>

#include "gen_array.h"
#include "gen_grid.h"

#define ESPSR_JOB_MAGIC 0x4A525345u    // "ESRJ"
#define ESPSR_RESULT_MAGIC 0x52525345u // "ESRR"
#define ESPSR_JOB_VERSION 3u
#define ESPSR_JOB_NAME_BYTES 40u
#define ESPSR_JOB_MODEL_BYTES 16u
#define ESPSR_JOB_VARIANTS_MAX 24u
#define ESPSR_JOB_NO_SOURCE (-1)   // a variant fed by the input itself
#define ESPSR_JOB_AFE_CHANNEL (-1) // BSS: the channel ESP-SR's AFE picks itself
#define ESPSR_JOB_ACK 0x06u        // the PC's byte after each result: link idle again
#define ESPSR_JOB_REFUSED 0x106    // ESP_ERR_NOT_SUPPORTED: ESP-SR lacks the combination

typedef enum {
    ESPSR_JOB_KIND_DSP_AFE = 0, // dsp_afe with spatial, ns_on, ns_floor_db
    ESPSR_JOB_KIND_BSS = 1,     // ESP-SR's speech-recognition AFE, two mics, SE only
    ESPSR_JOB_KIND_WEBRTC = 2,  // ESP-SR's ns_pro_create at level, on source's output
    ESPSR_JOB_KIND_NSNET = 3,   // ESP-SR's net NS called model, on source's output
} espsr_job_kind_t;

typedef struct {
    char name[ESPSR_JOB_NAME_BYTES];
    char model[ESPSR_JOB_MODEL_BYTES];
    uint32_t kind;     // espsr_job_kind_t
    int32_t source;    // index of the variant feeding this one
    uint32_t spatial;  // dsp_afe_spatial_t
    uint32_t ns_on;    // 1: OM-LSA in the ns slot, 0: unity gains
    float ns_floor_db; // OM-LSA G_min
    int32_t level;     // ns_pro_create mode: 0 mild, 1 medium, 2 aggressive
    int32_t channel;   // BSS: raw_data channel out, or ESPSR_JOB_AFE_CHANNEL
} espsr_job_variant_t;

typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t job;
    uint32_t samples;    // per channel of the input that follows
    uint32_t channels;   // GEN_ARRAY_N_MICS
    uint32_t n_variants; // 0 ends the session
    char item[ESPSR_JOB_NAME_BYTES];
    float balance[2 * GEN_GRID_N_BINS]; // calib/bal of board B: re, im per bin
    espsr_job_variant_t variants[ESPSR_JOB_VARIANTS_MAX];
} espsr_job_t;

typedef struct {
    uint32_t magic;
    uint32_t job;
    uint32_t index;          // of the variant in the job
    int32_t status;          // esp_err_t; an output follows only on ESP_OK
    uint32_t crc32;          // zlib's CRC32 of the output bytes
    uint32_t samples;        // mono samples that follow
    uint32_t us_mean;        // per GEN_GRID_HOP_SAMPLES of audio
    uint32_t us_peak;        // per GEN_GRID_HOP_SAMPLES of audio
    uint32_t internal_bytes; // heap the variant held while it ran
    uint32_t psram_bytes;
    int32_t channel; // BSS: trigger_channel_id at the end; else -1
} espsr_job_result_t;

_Static_assert(sizeof(espsr_job_variant_t) == 84, "items.py packs 84-byte variants");
_Static_assert(sizeof(espsr_job_t) ==
                   24 + ESPSR_JOB_NAME_BYTES + 8 * GEN_GRID_N_BINS + 84 * ESPSR_JOB_VARIANTS_MAX,
               "items.py packs the job without padding");
_Static_assert(sizeof(espsr_job_result_t) == 44, "items.py reads 44-byte results");
