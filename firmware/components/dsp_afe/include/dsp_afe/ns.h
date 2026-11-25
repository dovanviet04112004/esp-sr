/** The ns slot: one frame of the spatial output's complex bins in, per-bin gains out (KEHOACH 3.2, 3.9,
 * ADR-0047). dsp_afe owns the OM-LSA floor; any other implementation plugs in through dsp_afe_ns_ops_t.
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
    // bins: GEN_GRID_N_BINS complex values, re then im each
    esp_err_t (*process)(void *ctx, void *state, const float *bins, const float *echo_power, float *gain,
                         float *speech_prob);
} dsp_afe_ns_ops_t;

typedef struct {
    float floor_db; // G_min in dB, <= 0, read every hop
} dsp_afe_ns_omlsa_config_t;

/** The OM-LSA plus IMCRA floor as a slot implementation; its ctx is a dsp_afe_ns_omlsa_config_t, the rest of
 * its constants come from gen_afe.h. process takes the bins' power re^2 + im^2 itself; echo_power is
 * GEN_GRID_N_BINS floats or NULL; gain gets GEN_GRID_N_BINS values in 0..1 and speech_prob one value in 0..1.
 *  @ctx any | non-blocking | returns a static table
 */
const dsp_afe_ns_ops_t *dsp_afe_ns_omlsa_ops(void);

/** One frame of OM-LSA from the bins' power, the slot's process after its power step, for the golden and the
 *  bench: power is GEN_GRID_N_BINS floats; cfg, state and the rest as the slot's.
 *  @ctx any | non-blocking | caller owns state, sized and set up by the slot's state_bytes and init
 */
esp_err_t dsp_afe_ns_omlsa_power(const dsp_afe_ns_omlsa_config_t *cfg, void *state, const float *power,
                                 const float *echo_power, float *gain, float *speech_prob);

#ifdef __cplusplus
}
#endif
