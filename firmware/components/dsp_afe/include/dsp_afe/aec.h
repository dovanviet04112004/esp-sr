/** Multidelay block frequency-domain echo canceller, one per microphone (KEHOACH 3.5).
 *  Overlap-save on blocks of GEN_GRID_HOP_SAMPLES with its own FFT; runs ahead of the STFT.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t n_mics;
    uint8_t n_partitions;        // 8 gives a 128 ms tail
    uint32_t bulk_delay_samples; // NVS calib/aec_delay
} dsp_afe_aec_config_t;

typedef struct {
    float erle_db; // smoothed estimate, per microphone mean
    bool diverged;
    uint32_t divergence_resets;
} dsp_afe_aec_stats_t;

typedef struct dsp_afe_aec_s dsp_afe_aec_t;

/** Bytes this configuration needs; all of it is touched every frame (KEHOACH 6.5).
 *  @ctx any | non-blocking
 */
size_t dsp_afe_aec_workspace_bytes(const dsp_afe_aec_config_t *cfg);

/** Build the canceller in mem with zero weights.
 *  @ctx task | non-blocking | caller owns mem
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE
 */
esp_err_t dsp_afe_aec_init(dsp_afe_aec_t **out, const dsp_afe_aec_config_t *cfg, void *mem, size_t bytes);

/** Cancel one hop: mic[i] and ref are GEN_GRID_HOP_SAMPLES each, mic is overwritten with the error.
 *  residual_power gets GEN_GRID_N_BINS floats for the ns slot; it may be NULL.
 *  @ctx any | non-blocking
 */
esp_err_t dsp_afe_aec_process(dsp_afe_aec_t *st, float *const *mic, const float *ref, float *residual_power);

/** Current figures of merit.
 *  @ctx any | non-blocking
 */
void dsp_afe_aec_stats(const dsp_afe_aec_t *st, dsp_afe_aec_stats_t *out);

#ifdef __cplusplus
}
#endif
