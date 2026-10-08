/** Log-mel filterbank on the grid's bins, and MFCC kept for comparison (KEHOACH 3.11).
 *  @ctx any | non-blocking | filters are built once at init, never per frame
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "dsp_spec/fft.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DSP_SPEC_MEL_MAX_BANDS 80

typedef struct {
    uint16_t n_bands; // 1..DSP_SPEC_MEL_MAX_BANDS
    float f_min_hz;
    float f_max_hz;  // at most half the sample rate
    float log_floor; // added to power inside the log
} dsp_spec_mel_config_t;

typedef struct dsp_spec_mel_s dsp_spec_mel_t;

/** Bytes a filterbank with this configuration needs.
 *  @ctx any | non-blocking
 *  @ret 0 for an invalid configuration
 */
size_t dsp_spec_mel_workspace_bytes(const dsp_spec_mel_config_t *cfg);

/** Build triangular Slaney-style filters in mem.
 *  @ctx task | non-blocking | caller owns mem
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE short mem
 */
esp_err_t dsp_spec_mel_init(dsp_spec_mel_t **out, const dsp_spec_mel_config_t *cfg, void *mem, size_t bytes);

/** Natural log of each band's power from GEN_GRID_N_BINS bins; writes n_bands floats.
 *  The log is the module's float32 one, within 2e-6 of ln, as srpipe.dsp.spec.mel takes it (KEHOACH 3.14).
 *  @ctx any | non-blocking
 */
esp_err_t dsp_spec_mel_log(const dsp_spec_mel_t *mel, const dsp_spec_cplx_t *bins, float *out);

/** Orthonormal DCT-II of n_bands log energies into n_ceps coefficients, n_ceps <= n_bands.
 *  @ctx any | non-blocking
 */
esp_err_t dsp_spec_mel_mfcc(const dsp_spec_mel_t *mel, const float *log_mel, float *ceps, size_t n_ceps);

#ifdef __cplusplus
}
#endif
