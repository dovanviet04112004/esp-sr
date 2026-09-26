#include "dsp_afe/aec.h"

#include <string.h>

#include "afe_internal.h"
#include "gen_array.h"
#include "gen_grid.h"

// Neutral shell of E3-T4: microphones pass unchanged until E10-T4 writes the canceller (KEHOACH 3.5).

struct dsp_afe_aec_s {
    dsp_spec_fft_t *fft;
    uint8_t n_mics;
    dsp_afe_aec_stats_t stats;
};

static bool config_ok(const dsp_afe_aec_config_t *cfg)
{
    return cfg != NULL && cfg->n_mics >= 1 && cfg->n_mics <= GEN_ARRAY_N_MICS && cfg->n_partitions >= 1;
}

size_t dsp_afe_aec_workspace_bytes(const dsp_afe_aec_config_t *cfg)
{
    return config_ok(cfg) ? afe_region_bytes(sizeof(struct dsp_afe_aec_s)) : 0;
}

esp_err_t dsp_afe_aec_init(dsp_afe_aec_t **out, const dsp_afe_aec_config_t *cfg, dsp_spec_fft_t *fft,
                           void *mem, size_t bytes)
{
    if (out == NULL || fft == NULL || mem == NULL || !config_ok(cfg)) { return ESP_ERR_INVALID_ARG; }
    dsp_afe_aec_t *st = afe_state(mem, bytes, sizeof(*st));
    if (st == NULL) { return ESP_ERR_INVALID_SIZE; }
    memset(st, 0, sizeof(*st));
    st->fft = fft;
    st->n_mics = cfg->n_mics;
    *out = st;
    return ESP_OK;
}

esp_err_t dsp_afe_aec_process(dsp_afe_aec_t *st, float *const *mic, const float *ref, float *residual_power)
{
    if (st == NULL || mic == NULL || ref == NULL) { return ESP_ERR_INVALID_ARG; }
    if (residual_power != NULL) { memset(residual_power, 0, GEN_GRID_N_BINS * sizeof(float)); }
    return ESP_OK;
}

void dsp_afe_aec_stats(const dsp_afe_aec_t *st, dsp_afe_aec_stats_t *out)
{
    *out = st->stats;
}
