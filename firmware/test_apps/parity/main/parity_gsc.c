#include <stdlib.h>

#include "dsp_afe/gsc.h"
#include "gen_grid.h"
#include "parity.h"

#define FLOATS_PER_BIN 2
#define CONFIG_VALUES 5 // spacing, c, step, leakage, cap

static bool run_hops(const float *config, const float *bins0, const float *bins1, const float *angle,
                     const float *adapt, size_t hops, float *out)
{
    const dsp_afe_gsc_config_t cfg = {.spacing_m = config[0],
                                      .speed_of_sound_m_s = config[1],
                                      .step_size = config[2],
                                      .leakage = config[3],
                                      .weight_max = config[4]};
    const size_t bytes = dsp_afe_gsc_workspace_bytes(&cfg);
    void *mem = bytes > 0 ? malloc(bytes) : NULL;
    dsp_afe_gsc_t *gsc = NULL;
    bool ok = mem != NULL && dsp_afe_gsc_init(&gsc, &cfg, mem, bytes) == ESP_OK;
    const size_t stride = GEN_GRID_N_BINS * FLOATS_PER_BIN;
    for (size_t h = 0; ok && h < hops; h++) {
        ok = dsp_afe_gsc_process(gsc, (const dsp_spec_cplx_t *)(bins0 + h * stride),
                                 (const dsp_spec_cplx_t *)(bins1 + h * stride), angle[h], adapt[h] != 0.0f,
                                 (dsp_spec_cplx_t *)(out + h * stride)) == ESP_OK;
    }
    free(mem);
    return ok;
}

bool parity_gsc(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t config, bins0, bins1, angle, adapt, out;
    if (!parity_tensor(buf, len, "config", &config) || !parity_tensor(buf, len, "bins0", &bins0) ||
        !parity_tensor(buf, len, "bins1", &bins1) || !parity_tensor(buf, len, "angle", &angle) ||
        !parity_tensor(buf, len, "adapt", &adapt) || !parity_tensor(buf, len, "out", &out) ||
        bins0.ndim != 3 || bins0.dims[1] != GEN_GRID_N_BINS || bins0.dims[2] != FLOATS_PER_BIN) {
        return false;
    }
    const size_t hops = bins0.dims[0];
    const size_t n = hops * GEN_GRID_N_BINS * FLOATS_PER_BIN;
    float cfg[CONFIG_VALUES];
    float *in = malloc((2 * n + 2 * hops) * sizeof(float));
    float *want = malloc(n * sizeof(float));
    float *got = malloc(n * sizeof(float));
    const bool ok = in != NULL && want != NULL && got != NULL && parity_floats(&config, cfg, CONFIG_VALUES) &&
                    parity_floats(&bins0, in, n) && parity_floats(&bins1, in + n, n) &&
                    parity_floats(&angle, in + 2 * n, hops) &&
                    parity_floats(&adapt, in + 2 * n + hops, hops) && parity_floats(&out, want, n) &&
                    run_hops(cfg, in, in + n, in + 2 * n, in + 2 * n + hops, hops, got);
    if (ok) { parity_report("gsc", case_name, "out", want, got, n); }
    free(got);
    free(want);
    free(in);
    return ok;
}
