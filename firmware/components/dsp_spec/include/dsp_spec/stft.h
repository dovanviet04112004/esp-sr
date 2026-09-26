/** Streaming STFT and weighted overlap-add synthesis on the grid of gen_grid.h (KEHOACH 3.1).
 *  One hop in gives one spectrum out; one spectrum in gives one hop out, one window late.
 */
#pragma once

#include <stddef.h>

#include "dsp_spec/fft.h"
#include "esp_err.h"
#include "gen_grid.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct dsp_spec_stft_s dsp_spec_stft_t;
typedef struct dsp_spec_istft_s dsp_spec_istft_t;

/** Bytes one analyser needs, excluding the shared FFT.
 *  @ctx any | non-blocking
 */
size_t dsp_spec_stft_workspace_bytes(void);

/** Build an analyser in mem that runs its transforms on fft, which several analysers may share.
 *  @ctx task | non-blocking | caller owns mem and fft; fft must be GEN_GRID_FFT_SIZE points
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE short mem
 */
esp_err_t dsp_spec_stft_init(dsp_spec_stft_t **out, dsp_spec_fft_t *fft, void *mem, size_t bytes);

/** Push GEN_GRID_HOP_SAMPLES samples; write the GEN_GRID_N_BINS bins of the latest window.
 *  @ctx any | non-blocking | the shared fft must not be in use by another task
 */
esp_err_t dsp_spec_stft_analyze(dsp_spec_stft_t *st, const float *hop, dsp_spec_cplx_t *bins);

/** Forget buffered samples, as after a gap in the frame sequence.
 *  @ctx any | non-blocking
 */
void dsp_spec_stft_reset(dsp_spec_stft_t *st);

/** Bytes one synthesiser needs, excluding the shared FFT.
 *  @ctx any | non-blocking
 */
size_t dsp_spec_istft_workspace_bytes(void);

/** Build a synthesiser in mem on a shared fft of GEN_GRID_FFT_SIZE points.
 *  @ctx task | non-blocking | caller owns mem and fft
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE short mem
 */
esp_err_t dsp_spec_istft_init(dsp_spec_istft_t **out, dsp_spec_fft_t *fft, void *mem, size_t bytes);

/** Take GEN_GRID_N_BINS bins; write the next GEN_GRID_HOP_SAMPLES output samples.
 *  @ctx any | non-blocking | the shared fft must not be in use by another task
 */
esp_err_t dsp_spec_istft_synthesize(dsp_spec_istft_t *st, const dsp_spec_cplx_t *bins, float *hop);

/** Clear the overlap buffer.
 *  @ctx any | non-blocking
 */
void dsp_spec_istft_reset(dsp_spec_istft_t *st);

#ifdef __cplusplus
}
#endif
