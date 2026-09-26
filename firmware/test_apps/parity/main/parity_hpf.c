#include <stdlib.h>

#include "dsp_afe/hpf.h"
#include "gen_array.h"
#include "gen_grid.h"
#include "parity.h"

static bool filter_hops(float cutoff_hz, float *samples, size_t per_channel)
{
    const dsp_afe_hpf_config_t cfg = {.cutoff_hz = cutoff_hz, .n_channels = GEN_ARRAY_N_MICS};
    const size_t bytes = dsp_afe_hpf_workspace_bytes(&cfg);
    void *mem = bytes > 0 ? malloc(bytes) : NULL;
    dsp_afe_hpf_t *hpf = NULL;
    bool ok = mem != NULL && dsp_afe_hpf_init(&hpf, &cfg, mem, bytes) == ESP_OK;
    for (size_t ch = 0; ok && ch < GEN_ARRAY_N_MICS; ch++) {
        for (size_t at = 0; ok && at < per_channel; at += GEN_GRID_HOP_SAMPLES) {
            float *hop = samples + ch * per_channel + at;
            ok = dsp_afe_hpf_process(hpf, (uint8_t)ch, hop, GEN_GRID_HOP_SAMPLES) == ESP_OK;
        }
    }
    free(mem);
    return ok;
}

bool parity_hpf(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t input, output, cutoff;
    if (!parity_tensor(buf, len, "input", &input) || !parity_tensor(buf, len, "output", &output) ||
        !parity_tensor(buf, len, "cutoff_hz", &cutoff) || input.ndim != 2 ||
        input.dims[0] != GEN_ARRAY_N_MICS || input.dims[1] % GEN_GRID_HOP_SAMPLES != 0) {
        return false;
    }
    const size_t n = (size_t)input.dims[0] * input.dims[1];
    float cutoff_hz = 0.0f;
    float *got = malloc(n * sizeof(float));
    float *want = malloc(n * sizeof(float));
    const bool ok = got != NULL && want != NULL && parity_floats(&cutoff, &cutoff_hz, 1) &&
                    parity_floats(&input, got, n) && parity_floats(&output, want, n) &&
                    filter_hops(cutoff_hz, got, input.dims[1]);
    if (ok) { parity_report("hpf", case_name, "output", want, got, n); }
    free(want);
    free(got);
    return ok;
}
