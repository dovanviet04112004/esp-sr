#include "dsp_afe/balance.h"

// Neutral shell of E3-T4: ch1 passes unchanged until E7-T2 applies the calibrated gains (KEHOACH 3.4).

void dsp_afe_balance_apply(const dsp_spec_cplx_t *gain, dsp_spec_cplx_t *ch1_bins)
{
    (void)gain;
    (void)ch1_bins;
}
