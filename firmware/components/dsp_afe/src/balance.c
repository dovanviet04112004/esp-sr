#include "dsp_afe/balance.h"

#include "gen_grid.h"

void dsp_afe_balance_apply(const dsp_spec_cplx_t *gain, dsp_spec_cplx_t *ch1_bins)
{
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        const dsp_spec_cplx_t g = gain[k];
        const dsp_spec_cplx_t x = ch1_bins[k];
        ch1_bins[k].re = g.re * x.re - g.im * x.im;
        ch1_bins[k].im = g.re * x.im + g.im * x.re;
    }
}
