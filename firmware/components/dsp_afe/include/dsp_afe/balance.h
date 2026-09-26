/** Static amplitude and phase match of ch1 to ch0, one complex gain per bin (KEHOACH 3.4).
 *  Gains come from test_apps/calib through NVS calib/bal.
 */
#pragma once

#include "dsp_spec/fft.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Multiply GEN_GRID_N_BINS bins of ch1 by the calibrated gains, in place.
 *  @ctx any | non-blocking | stateless
 */
void dsp_afe_balance_apply(const dsp_spec_cplx_t *gain, dsp_spec_cplx_t *ch1_bins);

#ifdef __cplusplus
}
#endif
