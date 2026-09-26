#include <stdlib.h>

#include "dsp_afe/balance.h"
#include "gen_grid.h"
#include "parity.h"

#define FLOATS_PER_BIN 2

static bool shaped(const gold_tensor_t *bins, const gold_tensor_t *gains, const gold_tensor_t *output)
{
    return bins->ndim == 3 && bins->dims[1] == GEN_GRID_N_BINS && bins->dims[2] == FLOATS_PER_BIN &&
           output->ndim == 3 && output->dims[0] == bins->dims[0] && gains->ndim == 2 &&
           gains->dims[0] == GEN_GRID_N_BINS && gains->dims[1] == FLOATS_PER_BIN;
}

bool parity_balance(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t bins, gains, output;
    if (!parity_tensor(buf, len, "bins", &bins) || !parity_tensor(buf, len, "gains", &gains) ||
        !parity_tensor(buf, len, "output", &output) || !shaped(&bins, &gains, &output)) {
        return false;
    }
    const size_t hops = bins.dims[0];
    const size_t n = hops * GEN_GRID_N_BINS * FLOATS_PER_BIN;
    dsp_spec_cplx_t *gain = malloc(GEN_GRID_N_BINS * sizeof(dsp_spec_cplx_t));
    dsp_spec_cplx_t *got = malloc(hops * GEN_GRID_N_BINS * sizeof(dsp_spec_cplx_t));
    float *want = malloc(n * sizeof(float));
    const bool ok = gain != NULL && got != NULL && want != NULL &&
                    parity_floats(&gains, (float *)gain, GEN_GRID_N_BINS * FLOATS_PER_BIN) &&
                    parity_floats(&bins, (float *)got, n) && parity_floats(&output, want, n);
    for (size_t h = 0; ok && h < hops; h++) {
        dsp_afe_balance_apply(gain, got + h * GEN_GRID_N_BINS);
    }
    if (ok) { parity_report("balance", case_name, "output", want, (const float *)got, n); }
    free(want);
    free(got);
    free(gain);
    return ok;
}
