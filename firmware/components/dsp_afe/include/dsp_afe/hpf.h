/** Second-order Butterworth high-pass per microphone, run first in the chain (KEHOACH 3.4).
 *  @ctx any | non-blocking | one instance per task
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    float cutoff_hz; // 80 in the chain
    uint8_t n_channels;
} dsp_afe_hpf_config_t;

typedef struct dsp_afe_hpf_s dsp_afe_hpf_t;

/** Bytes this configuration needs.
 *  @ctx any | non-blocking
 */
size_t dsp_afe_hpf_workspace_bytes(const dsp_afe_hpf_config_t *cfg);

/** Design the biquad for the grid's sample rate and clear its state.
 *  @ctx any | non-blocking | caller owns mem
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG cutoff outside 10 Hz .. fs/4 | ESP_ERR_INVALID_SIZE
 */
esp_err_t dsp_afe_hpf_init(dsp_afe_hpf_t **out, const dsp_afe_hpf_config_t *cfg, void *mem, size_t bytes);

/** Filter n samples of one channel in place.
 *  @ctx any | non-blocking
 */
esp_err_t dsp_afe_hpf_process(dsp_afe_hpf_t *st, uint8_t channel, float *samples, size_t n);

#ifdef __cplusplus
}
#endif
