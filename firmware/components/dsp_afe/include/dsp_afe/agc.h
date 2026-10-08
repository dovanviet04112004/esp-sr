/** Two-stage level control: slow speech-level gain, fast look-ahead limiter (KEHOACH 3.10).
 *  The limiter delays the output by lookahead_ms and keeps every sample under limit_dbfs.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    float target_dbfs; // speech level to reach, -26
    float gain_min_db;
    float gain_max_db;
    float up_db_per_s;         // 3
    float down_db_per_s;       // 6
    float limit_dbfs;          // -3
    float lookahead_ms;        // 4
    float level_tau_s;         // speech level time constant, 2
    float level_gate_db;       // hops this far under the level are left out
    float level_fall_db_per_s; // while every speech hop is left out
    float release_ms;          // limiter gain back to 1
} dsp_afe_agc_config_t;

typedef struct dsp_afe_agc_s dsp_afe_agc_t;

/** Bytes this configuration needs, the look-ahead line included.
 *  @ctx any | non-blocking
 */
size_t dsp_afe_agc_workspace_bytes(const dsp_afe_agc_config_t *cfg);

/** Build the controller in mem at 0 dB gain.
 *  @ctx task | non-blocking | caller owns mem
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE
 */
esp_err_t dsp_afe_agc_init(dsp_afe_agc_t **out, const dsp_afe_agc_config_t *cfg, void *mem, size_t bytes);

/** Scale one hop in place; the slow gain moves only when speech is set, and freezes otherwise.
 *  @ctx any | non-blocking | gain_db gets the gain applied to this hop
 */
esp_err_t dsp_afe_agc_process(dsp_afe_agc_t *st, float *hop, bool speech, float *gain_db);

/** Move the target level, as SET_CONFIG afe/agc_target_dbfs asks.
 *  @ctx any | non-blocking
 */
void dsp_afe_agc_set_target(dsp_afe_agc_t *st, float target_dbfs);

/** Return the level, gain, limiter and lookahead to where init leaves them; coefficients and target stay.
 *  @ctx any | non-blocking
 */
void dsp_afe_agc_reset(dsp_afe_agc_t *st);

/** Empty the lookahead and release the limiter, the state that holds samples, as after a short gap; the slow
 *  gain and the speech level stay (KEHOACH 4.5.5).
 *  @ctx any | non-blocking
 */
void dsp_afe_agc_flush(dsp_afe_agc_t *st);

#ifdef __cplusplus
}
#endif
