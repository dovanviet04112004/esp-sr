/** Real FFT and inverse: the hand-written radix 4 that matches srpipe.dsp.spec.fft bit for bit, or dl_fft
 *  when Kconfig DSP_SPEC_FFT_BACKEND picks it (KEHOACH 3.1, 3.14, ADR-0020).
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

/** Bytes the caller provides for an FFT of n_points; dl_fft keeps its tables apart (KEHOACH 4.5.3 rule 7).
 *  @ctx any | non-blocking
 *  @ret 0 when n_points is not a power of two between 64 and 2048
 */
size_t dsp_spec_fft_workspace_bytes(size_t n_points);

/** Build an FFT of n_points inside mem, its tables computed once here; dl_fft puts them in internal RAM.
 *  @ctx task | non-blocking, dl_fft allocates once | init only; caller owns mem of the workspace bytes
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE short mem | ESP_ERR_NO_MEM no room for dl_fft
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
