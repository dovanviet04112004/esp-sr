/** SwiftF0's three Kaldi-scale pitch dims, streaming: POV, normalised log F0 and delta of a frame, given once
 * right_frames have followed it (KEHOACH 3.11 step 3.3).
 *  @ctx any | non-blocking | one stream per instance; caller owns the workspace
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DSP_SPEC_SWIFT_DIMS 3 // POV, normalised log F0, delta

typedef struct {
    float confidence; // a frame is voiced at this or above
    int left_frames;  // of the log F0 mean, behind the frame given
    int right_frames; // and ahead of it, the frames it waits for
    float pov_scale;
    float pitch_scale;
    float delta_scale;
} dsp_spec_swift_dims_config_t;

typedef struct dsp_spec_swift_dims_s dsp_spec_swift_dims_t;

/** Bytes a stream of cfg needs; 0 for a config it refuses.
 *  @ctx any | non-blocking
 */
size_t dsp_spec_swift_dims_workspace_bytes(const dsp_spec_swift_dims_config_t *cfg);

/** Build the dims in mem from a copy of cfg.
 *  @ctx any | non-blocking | caller owns mem
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG a config it refuses | ESP_ERR_INVALID_SIZE short mem
 */
esp_err_t dsp_spec_swift_dims_init(dsp_spec_swift_dims_t **out, const dsp_spec_swift_dims_config_t *cfg,
                                   void *mem, size_t bytes);

/** Forget every frame, as at the start of a stream.
 *  @ctx any | non-blocking
 */
void dsp_spec_swift_dims_reset(dsp_spec_swift_dims_t *dims);

/** Take one frame's log F0 and confidence; once right_frames have followed the frame they wait for, write its
 * dims to out and set ready.
 *  @ctx any | non-blocking | out holds DSP_SPEC_SWIFT_DIMS floats
 */
esp_err_t dsp_spec_swift_dims_push(dsp_spec_swift_dims_t *dims, float log_f0, float confidence, float *out,
                                   bool *ready);

#ifdef __cplusplus
}
#endif
