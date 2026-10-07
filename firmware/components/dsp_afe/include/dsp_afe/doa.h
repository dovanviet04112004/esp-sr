/** GCC-PHAT on the smoothed cross-spectrum, searched over an angle grid (KEHOACH 3.6).
 *  Angles follow contracts/array.yaml: 0 deg faces ch1, 90 deg is broadside, front and back alike.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "dsp_spec/fft.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    float spacing_m;
    float speed_of_sound_m_s;
    float band_min_hz;   // 200 in the chain
    float band_max_hz;   // spatial alias frequency by default
    float grid_step_deg; // 2 in the chain
    float smooth_tau_s;  // cross-spectrum smoothing, 0.2
} dsp_afe_doa_config_t;

typedef struct {
    int16_t angle_deg;  // 0..180, -1 until the first update
    uint8_t confidence; // peak over mean of the response, 0..255
} dsp_afe_doa_result_t;

typedef struct dsp_afe_doa_s dsp_afe_doa_t;

/** Bytes this configuration needs.
 *  @ctx any | non-blocking
 */
size_t dsp_afe_doa_workspace_bytes(const dsp_afe_doa_config_t *cfg);

/** Build the searcher in mem.
 *  @ctx task | non-blocking | caller owns mem
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE
 */
esp_err_t dsp_afe_doa_init(dsp_afe_doa_t **out, const dsp_afe_doa_config_t *cfg, void *mem, size_t bytes);

/** Fold one frame of both channels into the cross-spectrum; search the grid only when update is set.
 *  @ctx any | non-blocking | out keeps the last estimate when update is false
 */
esp_err_t dsp_afe_doa_process(dsp_afe_doa_t *st, const dsp_spec_cplx_t *x0, const dsp_spec_cplx_t *x1,
                              bool update, dsp_afe_doa_result_t *out);

/** Forget the smoothed cross-spectrum and the last estimate as init leaves them; the phasor tables stay.
 *  @ctx any | non-blocking
 */
void dsp_afe_doa_reset(dsp_afe_doa_t *st);

#ifdef __cplusplus
}
#endif
