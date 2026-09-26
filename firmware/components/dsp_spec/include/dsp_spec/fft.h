/** Real FFT and inverse on one backend, dl_fft or esp-dsp, picked in Kconfig (KEHOACH 3.1).
 *  @ctx any | non-blocking | one instance is used by one task at a time
 */
#pragma once

#include <stddef.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    float re;
    float im;
} dsp_spec_cplx_t;

typedef struct dsp_spec_fft_s dsp_spec_fft_t;

/** Bytes the caller must provide for an FFT of n_points, tables and scratch included.
 *  @ctx any | non-blocking
 *  @ret 0 when n_points is not a power of two between 64 and 2048
 */
size_t dsp_spec_fft_workspace_bytes(size_t n_points);

/** Build an FFT of n_points inside mem, which the caller keeps alive and never frees under it.
 *  @ctx task | non-blocking | caller owns mem, at least dsp_spec_fft_workspace_bytes(n_points)
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG bad length or NULL | ESP_ERR_INVALID_SIZE short mem
 */
esp_err_t dsp_spec_fft_init(dsp_spec_fft_t **out, size_t n_points, void *mem, size_t bytes);

/** Spectrum of n_points real samples into n_points / 2 + 1 bins, DC first, unscaled.
 *  @ctx any | non-blocking | in is not modified
 */
esp_err_t dsp_spec_fft_forward(dsp_spec_fft_t *fft, const float *in, dsp_spec_cplx_t *out);

/** Real signal of n_points from n_points / 2 + 1 bins, scaled by 1 / n_points.
 *  @ctx any | non-blocking | imaginary parts of the DC and Nyquist bins are ignored
 */
esp_err_t dsp_spec_fft_inverse(dsp_spec_fft_t *fft, const dsp_spec_cplx_t *in, float *out);

#ifdef __cplusplus
}
#endif
