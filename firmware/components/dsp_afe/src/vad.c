#include "dsp_afe/vad.h"

#include <string.h>

#include "afe_internal.h"

// Neutral shell of E3-T4: no hop is speech until E7-T3 writes the GMM (KEHOACH 3.10).

#define VAD_AGGRESSIVENESS_MAX 3

struct dsp_afe_vad_s {
    uint8_t aggressiveness;
};

static bool config_ok(const dsp_afe_vad_config_t *cfg)
{
    return cfg != NULL && cfg->aggressiveness <= VAD_AGGRESSIVENESS_MAX;
}

size_t dsp_afe_vad_workspace_bytes(const dsp_afe_vad_config_t *cfg)
{
    return config_ok(cfg) ? afe_region_bytes(sizeof(struct dsp_afe_vad_s)) : 0;
}

esp_err_t dsp_afe_vad_init(dsp_afe_vad_t **out, const dsp_afe_vad_config_t *cfg, void *mem, size_t bytes)
{
    if (out == NULL || mem == NULL || !config_ok(cfg)) { return ESP_ERR_INVALID_ARG; }
    dsp_afe_vad_t *st = afe_state(mem, bytes, sizeof(*st));
    if (st == NULL) { return ESP_ERR_INVALID_SIZE; }
    memset(st, 0, sizeof(*st));
    st->aggressiveness = cfg->aggressiveness;
    *out = st;
    return ESP_OK;
}

esp_err_t dsp_afe_vad_process(dsp_afe_vad_t *st, const float *hop, bool *speech)
{
    if (st == NULL || hop == NULL || speech == NULL) { return ESP_ERR_INVALID_ARG; }
    *speech = false;
    return ESP_OK;
}
