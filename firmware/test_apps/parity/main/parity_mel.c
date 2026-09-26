#include <stdio.h>
#include <stdlib.h>

#include "dsp_spec.h"
#include "esp_heap_caps.h"
#include "gen_grid.h"
#include "parity.h"

enum { CONFIG_BANDS = 0, CONFIG_F_MIN, CONFIG_F_MAX, CONFIG_FLOOR, CONFIG_CEPS, CONFIG_COUNT };

bool parity_mel(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t config, bins, log_mel, mfcc;
    if (!parity_tensor(buf, len, "config", &config) || config.dims[0] != CONFIG_COUNT ||
        !parity_tensor(buf, len, "bins", &bins) || !parity_tensor(buf, len, "log_mel", &log_mel) ||
        !parity_tensor(buf, len, "mfcc", &mfcc)) {
        return false;
    }
    const float *c = config.data;
    const dsp_spec_mel_config_t cfg = {
        .n_bands = (uint16_t)c[CONFIG_BANDS],
        .f_min_hz = c[CONFIG_F_MIN],
        .f_max_hz = c[CONFIG_F_MAX],
        .log_floor = c[CONFIG_FLOOR],
    };
    const size_t n_ceps = (size_t)c[CONFIG_CEPS];
    const size_t frames = bins.dims[0];
    const size_t bytes = dsp_spec_mel_workspace_bytes(&cfg);
    void *mem = malloc(bytes);
    float *got_log = heap_caps_malloc(frames * cfg.n_bands * sizeof(float), MALLOC_CAP_SPIRAM);
    float *got_mfcc = heap_caps_malloc(frames * n_ceps * sizeof(float), MALLOC_CAP_SPIRAM);
    dsp_spec_mel_t *bank = NULL;
    bool ok = mem != NULL && got_log != NULL && got_mfcc != NULL &&
              dsp_spec_mel_init(&bank, &cfg, mem, bytes) == ESP_OK;
    const dsp_spec_cplx_t *in = bins.data;
    for (size_t f = 0; ok && f < frames; f++) {
        ok = dsp_spec_mel_log(bank, in + f * GEN_GRID_N_BINS, got_log + f * cfg.n_bands) == ESP_OK &&
             dsp_spec_mel_mfcc(bank, got_log + f * cfg.n_bands, got_mfcc + f * n_ceps, n_ceps) == ESP_OK;
    }
    if (ok) {
        parity_report("mel", case_name, "log_mel", log_mel.data, got_log, frames * cfg.n_bands);
        parity_report("mel", case_name, "mfcc", mfcc.data, got_mfcc, frames * n_ceps);
    }
    free(mem);
    free(got_log);
    free(got_mfcc);
    return ok;
}
