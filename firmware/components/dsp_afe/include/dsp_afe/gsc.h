/** Two-microphone generalised sidelobe canceller in the STFT domain, steered by doa (KEHOACH 3.7).
 *  @ctx any | non-blocking | one instance per task
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "dsp_spec/fft.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    float spacing_m;
    float speed_of_sound_m_s;
    float step_size;  // NLMS mu, 0.05
    float leakage;    // 1e-4
    float weight_max; // norm cap per bin
} dsp_afe_gsc_config_t;

typedef struct dsp_afe_gsc_s dsp_afe_gsc_t;

/** Bytes this configuration needs.
 *  @ctx any | non-blocking
 */
size_t dsp_afe_gsc_workspace_bytes(const dsp_afe_gsc_config_t *cfg);

/** Build the canceller in mem with zero weights.
 *  @ctx task | non-blocking | caller owns mem
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE
 */
esp_err_t dsp_afe_gsc_init(dsp_afe_gsc_t **out, const dsp_afe_gsc_config_t *cfg, void *mem, size_t bytes);

/** Beam toward angle_deg and write GEN_GRID_N_BINS output bins; weights learn only when adapt is set.
 *  Set adapt only without a talker, or the canceller learns to remove them (KEHOACH 3.7).
 *  @ctx any | non-blocking
 */
esp_err_t dsp_afe_gsc_process(dsp_afe_gsc_t *st, const dsp_spec_cplx_t *x0, const dsp_spec_cplx_t *x1,
                              float angle_deg, bool adapt, dsp_spec_cplx_t *out);

#ifdef __cplusplus
}
#endif
