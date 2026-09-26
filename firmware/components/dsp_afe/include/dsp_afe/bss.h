/** Online AuxIVA for two sources and two microphones, IP2 updates, projection back (KEHOACH 3.8).
 *  @ctx any | non-blocking | one instance per task
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "dsp_spec/fft.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    float forget_tau_s; // weighted covariance memory, 1.0
    float spacing_m;    // used only to read directions
    float speed_of_sound_m_s;
} dsp_afe_bss_config_t;

typedef struct dsp_afe_bss_s dsp_afe_bss_t;

/** Bytes this configuration needs, most of it touched every frame.
 *  @ctx any | non-blocking
 */
size_t dsp_afe_bss_workspace_bytes(const dsp_afe_bss_config_t *cfg);

/** Build the separator in mem with identity demixing matrices.
 *  @ctx task | non-blocking | caller owns mem
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE
 */
esp_err_t dsp_afe_bss_init(dsp_afe_bss_t **out, const dsp_afe_bss_config_t *cfg, void *mem, size_t bytes);

/** Update and separate one frame into two outputs of GEN_GRID_N_BINS bins, scaled back to ch0.
 *  @ctx any | non-blocking
 */
esp_err_t dsp_afe_bss_process(dsp_afe_bss_t *st, const dsp_spec_cplx_t *x0, const dsp_spec_cplx_t *x1,
                              dsp_spec_cplx_t *y0, dsp_spec_cplx_t *y1);

/** Direction each output seems to come from, read off the mixing matrix; -1 when unclear.
 *  @ctx any | non-blocking | one candidate rule for choosing the talker's output (E8-T4)
 */
void dsp_afe_bss_directions(const dsp_afe_bss_t *st, int16_t angle_deg[2]);

#ifdef __cplusplus
}
#endif
