#include <stdlib.h>

#include "dsp_afe/ns.h"
#include "gen_grid.h"
#include "parity.h"

typedef struct {
    const float *power;
    const float *echo; // NULL for a case without residual echo
    float *gain;
    float *speech_prob;
} ns_io_t;

static bool run_hops(float floor_db, size_t hops, ns_io_t *io)
{
    dsp_afe_ns_omlsa_config_t cfg = {.floor_db = floor_db};
    const dsp_afe_ns_ops_t *ns = dsp_afe_ns_omlsa_ops();
    const size_t bytes = ns->state_bytes(&cfg);
    void *state = bytes > 0 ? malloc(bytes) : NULL;
    bool ok = state != NULL && ns->init(&cfg, state, bytes) == ESP_OK;
    for (size_t h = 0; ok && h < hops; h++) {
        const size_t at = h * GEN_GRID_N_BINS;
        ok = ns->process(&cfg, state, io->power + at, io->echo != NULL ? io->echo + at : NULL, io->gain + at,
                         &io->speech_prob[h]) == ESP_OK;
    }
    free(state);
    return ok;
}

bool parity_ns_omlsa(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t power, config, echo, gain, speech_prob;
    if (!parity_tensor(buf, len, "power", &power) || !parity_tensor(buf, len, "config", &config) ||
        !parity_tensor(buf, len, "gain", &gain) || !parity_tensor(buf, len, "speech_prob", &speech_prob) ||
        power.ndim != 2 || power.dims[1] != GEN_GRID_N_BINS) {
        return false;
    }
    const bool has_echo = parity_tensor(buf, len, "echo", &echo);
    const size_t hops = power.dims[0];
    const size_t n = hops * GEN_GRID_N_BINS;
    float floor_db = 0.0f;
    float *in = malloc(2 * n * sizeof(float));
    float *want = malloc((n + hops) * sizeof(float));
    float *got = malloc((n + hops) * sizeof(float));
    bool ok = in != NULL && want != NULL && got != NULL && parity_floats(&config, &floor_db, 1) &&
              parity_floats(&power, in, n) && (!has_echo || parity_floats(&echo, in + n, n)) &&
              parity_floats(&gain, want, n) && parity_floats(&speech_prob, want + n, hops);
    ns_io_t io = {.power = in, .echo = has_echo ? in + n : NULL, .gain = got, .speech_prob = got + n};
    ok = ok && run_hops(floor_db, hops, &io);
    if (ok) {
        parity_report("ns_omlsa", case_name, "gain", want, got, n);
        parity_report("ns_omlsa", case_name, "speech_prob", want + n, got + n, hops);
    }
    free(got);
    free(want);
    free(in);
    return ok;
}
