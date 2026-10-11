/** Front end of the speaker network ReDimNet2 b0 over one window of clean PCM (KEHOACH 3.17).
 *  @ctx any | non-blocking | tables are built once at init, never per window
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DSP_SPEC_SPK_FBANK_MAX_BANDS 128

typedef struct {
    uint32_t window_samples; // whole window, zeros ahead of a shorter one
    uint16_t frame_samples;  // symmetric Hamming
    uint16_t hop_samples;    // frames padded by half a hop at both ends
    uint16_t fft_size;       // bins 0 .. fft_size / 2 - 1
    uint16_t n_bands;        // 1..DSP_SPEC_SPK_FBANK_MAX_BANDS
    float f_min_hz;
    float f_max_hz;    // at most half the sample rate
    float preemphasis; // 0 <= preemphasis < 1
    float eps;         // of the normalisation, both clips and the log
} dsp_spec_spk_fbank_config_t;

typedef struct dsp_spec_spk_fbank_s dsp_spec_spk_fbank_t;

/** Bytes a front end with this configuration needs, its FFT included.
 *  @ctx any | non-blocking
 *  @ret 0 for an invalid configuration
 */
size_t dsp_spec_spk_fbank_workspace_bytes(const dsp_spec_spk_fbank_config_t *cfg);

/** Frames a window makes: the rows dsp_spec_spk_fbank_run writes.
 *  @ctx any | non-blocking
 *  @ret 0 for an invalid configuration
 */
size_t dsp_spec_spk_fbank_frames(const dsp_spec_spk_fbank_config_t *cfg);

/** Build the window, the mel weights and the FFT in mem.
 *  @ctx task | non-blocking, dl_fft allocates once | caller owns mem
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE short mem | the FFT's error
 */
esp_err_t dsp_spec_spk_fbank_init(dsp_spec_spk_fbank_t **out, const dsp_spec_spk_fbank_config_t *cfg,
                                  void *mem, size_t bytes);

/** Features of window_samples samples, frames x n_bands floats frame by frame, each band less its time mean.
 *  @ctx any | non-blocking | one window at a time per instance; pcm read; caller owns features
 */
esp_err_t dsp_spec_spk_fbank_run(dsp_spec_spk_fbank_t *fb, const int16_t *pcm, float *features);

#ifdef __cplusplus
}
#endif
