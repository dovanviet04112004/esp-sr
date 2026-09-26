#include <stdio.h>
#include <stdlib.h>

#include "dsp_spec.h"
#include "esp_heap_caps.h"
#include "gen_grid.h"
#include "parity.h"

#define FLOATS_PER_BIN 2

static struct {
    dsp_spec_fft_t *fft;
    dsp_spec_stft_t *stft;
    dsp_spec_istft_t *istft;
} s_chain;

static bool build_chain(void)
{
    if (s_chain.fft != NULL) { return true; }
    const size_t fft_bytes = dsp_spec_fft_workspace_bytes(GEN_GRID_FFT_SIZE);
    void *fft_mem = malloc(fft_bytes);
    void *stft_mem = malloc(dsp_spec_stft_workspace_bytes());
    void *istft_mem = malloc(dsp_spec_istft_workspace_bytes());
    return fft_mem != NULL && stft_mem != NULL && istft_mem != NULL &&
           dsp_spec_fft_init(&s_chain.fft, GEN_GRID_FFT_SIZE, fft_mem, fft_bytes) == ESP_OK &&
           dsp_spec_stft_init(&s_chain.stft, s_chain.fft, stft_mem, dsp_spec_stft_workspace_bytes()) ==
               ESP_OK &&
           dsp_spec_istft_init(&s_chain.istft, s_chain.fft, istft_mem, dsp_spec_istft_workspace_bytes()) ==
               ESP_OK;
}

bool parity_stft(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t signal, bins, rebuilt;
    if (!build_chain() || !parity_tensor(buf, len, "signal", &signal) ||
        !parity_tensor(buf, len, "bins", &bins) || !parity_tensor(buf, len, "rebuilt", &rebuilt)) {
        return false;
    }
    const size_t hops = signal.dims[0] / GEN_GRID_HOP_SAMPLES;
    dsp_spec_cplx_t *got_bins =
        heap_caps_malloc(hops * GEN_GRID_N_BINS * sizeof(dsp_spec_cplx_t), MALLOC_CAP_SPIRAM);
    float *got_rebuilt = heap_caps_malloc(hops * GEN_GRID_HOP_SAMPLES * sizeof(float), MALLOC_CAP_SPIRAM);
    if (got_bins == NULL || got_rebuilt == NULL) {
        free(got_bins);
        free(got_rebuilt);
        return false;
    }
    const float *in = signal.data;
    const dsp_spec_cplx_t *want_bins = bins.data;
    dsp_spec_stft_reset(s_chain.stft);
    dsp_spec_istft_reset(s_chain.istft);
    for (size_t h = 0; h < hops; h++) {
        dsp_spec_stft_analyze(s_chain.stft, in + h * GEN_GRID_HOP_SAMPLES, got_bins + h * GEN_GRID_N_BINS);
        dsp_spec_istft_synthesize(s_chain.istft, want_bins + h * GEN_GRID_N_BINS,
                                  got_rebuilt + h * GEN_GRID_HOP_SAMPLES);
    }
    parity_report("stft", case_name, "bins", bins.data, (const float *)got_bins,
                  hops * GEN_GRID_N_BINS * FLOATS_PER_BIN);
    parity_report("stft", case_name, "rebuilt", rebuilt.data, got_rebuilt, hops * GEN_GRID_HOP_SAMPLES);
    free(got_bins);
    free(got_rebuilt);
    return true;
}
