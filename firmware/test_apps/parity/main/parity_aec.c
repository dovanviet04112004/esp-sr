#include <stdlib.h>

#include "dsp_afe/aec.h"
#include "dsp_spec/fft.h"
#include "gen_grid.h"
#include "parity.h"

#define CONFIG_VALUES 3 // microphones, partitions, bulk delay
#define MICS_MAX 2

static bool run_hops(const float *config, const float *mic, const float *ref, size_t hops, float *out,
                     float *residual)
{
    const dsp_afe_aec_config_t cfg = {.n_mics = (uint8_t)config[0],
                                      .n_partitions = (uint8_t)config[1],
                                      .bulk_delay_samples = (uint32_t)config[2]};
    const size_t fft_bytes = dsp_spec_fft_workspace_bytes(GEN_GRID_FFT_SIZE);
    const size_t bytes = dsp_afe_aec_workspace_bytes(&cfg);
    void *fft_mem = malloc(fft_bytes);
    void *mem = bytes > 0 ? malloc(bytes) : NULL;
    dsp_spec_fft_t *fft = NULL;
    dsp_afe_aec_t *aec = NULL;
    bool ok = fft_mem != NULL && mem != NULL &&
              dsp_spec_fft_init(&fft, GEN_GRID_FFT_SIZE, fft_mem, fft_bytes) == ESP_OK &&
              dsp_afe_aec_init(&aec, &cfg, fft, mem, bytes) == ESP_OK;
    const size_t n = hops * GEN_GRID_HOP_SAMPLES;
    for (size_t m = 0; ok && m < cfg.n_mics; m++) {
        for (size_t i = 0; i < n; i++) {
            out[m * n + i] = mic[m * n + i];
        }
    }
    for (size_t h = 0; ok && h < hops; h++) {
        float *rows[MICS_MAX] = {out + h * GEN_GRID_HOP_SAMPLES, out + n + h * GEN_GRID_HOP_SAMPLES};
        ok = dsp_afe_aec_process(aec, rows, ref + h * GEN_GRID_HOP_SAMPLES, residual + h * GEN_GRID_N_BINS) ==
             ESP_OK;
    }
    free(mem);
    free(fft_mem);
    return ok;
}

bool parity_aec(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t config, mic, ref, out, residual;
    if (!parity_tensor(buf, len, "config", &config) || !parity_tensor(buf, len, "mic", &mic) ||
        !parity_tensor(buf, len, "ref", &ref) || !parity_tensor(buf, len, "out", &out) ||
        !parity_tensor(buf, len, "residual", &residual) || mic.ndim != 2 || mic.dims[0] > MICS_MAX) {
        return false;
    }
    const size_t mics = mic.dims[0];
    const size_t n = mic.dims[1];
    const size_t hops = n / GEN_GRID_HOP_SAMPLES;
    const size_t bins = hops * GEN_GRID_N_BINS;
    float cfg[CONFIG_VALUES];
    float *in = malloc((mics * n + n) * sizeof(float));
    float *want = malloc((mics * n + bins) * sizeof(float));
    float *got = malloc((mics * n + bins) * sizeof(float));
    const bool ok = in != NULL && want != NULL && got != NULL && parity_floats(&config, cfg, CONFIG_VALUES) &&
                    parity_floats(&mic, in, mics * n) && parity_floats(&ref, in + mics * n, n) &&
                    parity_floats(&out, want, mics * n) && parity_floats(&residual, want + mics * n, bins) &&
                    run_hops(cfg, in, in + mics * n, hops, got, got + mics * n);
    if (ok) {
        parity_report("aec", case_name, "out", want, got, mics * n);
        parity_report("aec", case_name, "residual", want + mics * n, got + mics * n, bins);
    }
    free(got);
    free(want);
    free(in);
    return ok;
}
