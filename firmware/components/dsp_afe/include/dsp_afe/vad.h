/** The WebRTC VAD in float32: a two-class Gaussian mixture on six band levels, then a hangover
 * (KEHOACH 3.10). Reads the clean signal ahead of agc, since its features are absolute energies.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "gen_afe.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t aggressiveness; // 0 most lenient .. 3 strictest
    uint16_t hangover_ms;   // 240 in the chain
} dsp_afe_vad_config_t;

typedef struct dsp_afe_vad_s dsp_afe_vad_t;

typedef struct {
    float level_db[GEN_AFE_VAD_BANDS]; // band levels, dB of int16-scale energy
    bool raw;                          // GMM decision ahead of the hangover
} dsp_afe_vad_detail_t;

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

/** What the last dsp_afe_vad_process saw: its six band levels and its decision ahead of the hangover.
 *  @ctx any | non-blocking | same task as dsp_afe_vad_process
 */
void dsp_afe_vad_detail(const dsp_afe_vad_t *st, dsp_afe_vad_detail_t *out);

#ifdef __cplusplus
}
#endif
