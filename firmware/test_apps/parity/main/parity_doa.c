#include <stdlib.h>

#include "dsp_afe/doa.h"
#include "gen_grid.h"
#include "parity.h"

#define FLOATS_PER_BIN 2
#define CONFIG_VALUES 6 // spacing, c, band min, max, step, tau

static bool run_hops(const float *config, const float *bins0, const float *bins1, const float *update,
                     size_t hops, float *angle, float *confidence)
{
    const dsp_afe_doa_config_t cfg = {.spacing_m = config[0],
                                      .speed_of_sound_m_s = config[1],
                                      .band_min_hz = config[2],
                                      .band_max_hz = config[3],
                                      .grid_step_deg = config[4],
                                      .smooth_tau_s = config[5]};
    const size_t bytes = dsp_afe_doa_workspace_bytes(&cfg);
    void *mem = bytes > 0 ? malloc(bytes) : NULL;
    dsp_afe_doa_t *doa = NULL;
    bool ok = mem != NULL && dsp_afe_doa_init(&doa, &cfg, mem, bytes) == ESP_OK;
    const size_t stride = GEN_GRID_N_BINS * FLOATS_PER_BIN;
    for (size_t h = 0; ok && h < hops; h++) {
        dsp_afe_doa_result_t out;
        ok = dsp_afe_doa_process(doa, (const dsp_spec_cplx_t *)(bins0 + h * stride),
                                 (const dsp_spec_cplx_t *)(bins1 + h * stride), update[h] != 0.0f,
                                 &out) == ESP_OK;
        angle[h] = (float)out.angle_deg;
        confidence[h] = (float)out.confidence;
    }
    free(mem);
    return ok;
}

bool parity_doa(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t config, bins0, bins1, update, angle, confidence;
    if (!parity_tensor(buf, len, "config", &config) || !parity_tensor(buf, len, "bins0", &bins0) ||
        !parity_tensor(buf, len, "bins1", &bins1) || !parity_tensor(buf, len, "update", &update) ||
        !parity_tensor(buf, len, "angle", &angle) || !parity_tensor(buf, len, "confidence", &confidence) ||
        bins0.ndim != 3 || bins0.dims[1] != GEN_GRID_N_BINS || bins0.dims[2] != FLOATS_PER_BIN) {
        return false;
    }
    const size_t hops = bins0.dims[0];
    const size_t n = hops * GEN_GRID_N_BINS * FLOATS_PER_BIN;
    float cfg[CONFIG_VALUES];
    float *in = malloc((2 * n + hops) * sizeof(float));
    float *want = malloc(2 * hops * sizeof(float));
    float *got = malloc(2 * hops * sizeof(float));
    const bool ok = in != NULL && want != NULL && got != NULL && parity_floats(&config, cfg, CONFIG_VALUES) &&
                    parity_floats(&bins0, in, n) && parity_floats(&bins1, in + n, n) &&
                    parity_floats(&update, in + 2 * n, hops) && parity_floats(&angle, want, hops) &&
                    parity_floats(&confidence, want + hops, hops) &&
                    run_hops(cfg, in, in + n, in + 2 * n, hops, got, got + hops);
    if (ok) {
        parity_report("doa", case_name, "angle", want, got, hops);
        parity_report("doa", case_name, "confidence", want + hops, got + hops, hops);
    }
    free(got);
    free(want);
    free(in);
    return ok;
}
