/** The ns slot: one frame of noisy power in, per-bin gains out (KEHOACH 3.2, 3.9).
 *  dsp_afe owns the OM-LSA floor; any other implementation plugs in through dsp_afe_ns_ops_t.
 */
#pragma once

#include <stddef.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    size_t (*state_bytes)(void *ctx);
    esp_err_t (*init)(void *ctx, void *state, size_t bytes);
    void (*reset)(void *ctx, void *state); // state as init leaves it, tables kept
    esp_err_t (*process)(void *ctx, void *state, const float *power, const float *echo_power, float *gain,
                         float *speech_prob);
} dsp_afe_ns_ops_t;

typedef struct {
    float floor_db; // G_min in dB, <= 0, read every hop
} dsp_afe_ns_omlsa_config_t;

/** The OM-LSA plus IMCRA floor as a slot implementation; its ctx is a dsp_afe_ns_omlsa_config_t, the rest of
 * its constants come from gen_afe.h. process: power and echo_power are GEN_GRID_N_BINS floats, echo_power may
 * be NULL; gain gets GEN_GRID_N_BINS values in 0..1 and speech_prob one value in 0..1.
 *  @ctx any | non-blocking | returns a static table
 */
const dsp_afe_ns_ops_t *dsp_afe_ns_omlsa_ops(void);

#ifdef __cplusplus
}
#endif
