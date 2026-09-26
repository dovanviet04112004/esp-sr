/** Two-class Gaussian mixture on six sub-band log energies, with hangover (KEHOACH 3.10).
 *  Reads the clean signal ahead of agc, since its features are absolute energies.
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
    uint8_t aggressiveness; // 0 most lenient .. 3 strictest
    uint16_t hangover_ms;   // 240 in the chain
} dsp_afe_vad_config_t;

typedef struct dsp_afe_vad_s dsp_afe_vad_t;

/** Bytes this configuration needs.
 *  @ctx any | non-blocking
 */
size_t dsp_afe_vad_workspace_bytes(const dsp_afe_vad_config_t *cfg);

/** Build the detector in mem with its initial model.
 *  @ctx task | non-blocking | caller owns mem
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE
 */
esp_err_t dsp_afe_vad_init(dsp_afe_vad_t **out, const dsp_afe_vad_config_t *cfg, void *mem, size_t bytes);

/** Classify one hop of GEN_GRID_HOP_SAMPLES samples; speech stays true through the hangover.
 *  @ctx any | non-blocking
 */
esp_err_t dsp_afe_vad_process(dsp_afe_vad_t *st, const float *hop, bool *speech);

#ifdef __cplusplus
}
#endif
