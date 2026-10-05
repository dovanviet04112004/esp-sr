/** Kaldi's pitch tracker in its own zero-latency online mode, one grid hop in, one frame out (KEHOACH 3.11).
 *  @ctx any | non-blocking | caller owns the workspace; one tracker per stream, reset only when it breaks
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DSP_SPEC_PITCH_FEATURES 3  // POV feature, normalised log pitch, delta
#define DSP_SPEC_PITCH_LEAD_HOPS 2 // hops after a reset that complete no frame

typedef struct {
    float resample_hz; // divides the grid rate into whole hops
    float lowpass_cutoff_hz;
    uint16_t lowpass_zeros;
    uint16_t upsample_zeros;
    float window_s; // NCCF window
    float min_f0_hz;
    float max_f0_hz;
    float soft_min_f0;
    float penalty_factor;
    float delta_pitch; // ratio between neighbouring lags, less one
    float nccf_ballast;
    float ballast_window_s;     // the ballast's mean square over the newest
    float normalization_left_s; // past of the log-pitch mean
    uint16_t delta_window;      // frames each side of the delta
    float pov_scale;
    float pitch_scale;
    float delta_pitch_scale;
} dsp_spec_pitch_config_t;

typedef struct dsp_spec_pitch_s dsp_spec_pitch_t;

/** Bytes a tracker with this configuration needs, tables and the traceback history included.
 *  @ctx any | non-blocking
 *  @ret 0 for an invalid configuration
 */
size_t dsp_spec_pitch_workspace_bytes(const dsp_spec_pitch_config_t *cfg);

/** Build the filter and lag tables in mem and start a stream.
 *  @ctx task | non-blocking, tables take milliseconds | caller owns mem, which may sit in PSRAM
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE short mem
 */
esp_err_t dsp_spec_pitch_init(dsp_spec_pitch_t **out, const dsp_spec_pitch_config_t *cfg, void *mem,
                              size_t bytes);

/** Forget the stream, as when it breaks.
 *  @ctx any | non-blocking
 */
void dsp_spec_pitch_reset(dsp_spec_pitch_t *pitch);

/** Take GEN_GRID_HOP_SAMPLES samples and write the DSP_SPEC_PITCH_FEATURES of the frame they complete; zeros
 *  for the first DSP_SPEC_PITCH_LEAD_HOPS hops after a reset.
 *  @ctx any | non-blocking | same caller as dsp_spec_pitch_reset
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG
 */
esp_err_t dsp_spec_pitch_frame(dsp_spec_pitch_t *pitch, const float *hop, float *features);

/** NCCF and F0 of the newest frame's best lag, zero until the first frame, for measurement.
 *  @ctx any | non-blocking
 */
void dsp_spec_pitch_latest(const dsp_spec_pitch_t *pitch, float *nccf, float *f0_hz);

#ifdef __cplusplus
}
#endif
