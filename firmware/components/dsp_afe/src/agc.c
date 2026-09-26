#include "dsp_afe/agc.h"

#include <string.h>

#include "afe_internal.h"

// Neutral shell of E3-T4: the gain stays 0 dB until E7-T4 writes both stages (KEHOACH 3.10).

struct dsp_afe_agc_s {
    float target_dbfs;
};

static bool config_ok(const dsp_afe_agc_config_t *cfg)
{
    return cfg != NULL && cfg->target_dbfs <= 0.0f && cfg->gain_min_db <= cfg->gain_max_db &&
           cfg->up_db_per_s > 0.0f && cfg->down_db_per_s > 0.0f && cfg->limit_dbfs <= 0.0f &&
           cfg->lookahead_ms >= 0.0f;
}

size_t dsp_afe_agc_workspace_bytes(const dsp_afe_agc_config_t *cfg)
{
    return config_ok(cfg) ? afe_region_bytes(sizeof(struct dsp_afe_agc_s)) : 0;
}

esp_err_t dsp_afe_agc_init(dsp_afe_agc_t **out, const dsp_afe_agc_config_t *cfg, void *mem, size_t bytes)
{
    if (out == NULL || mem == NULL || !config_ok(cfg)) { return ESP_ERR_INVALID_ARG; }
    dsp_afe_agc_t *st = afe_state(mem, bytes, sizeof(*st));
    if (st == NULL) { return ESP_ERR_INVALID_SIZE; }
    memset(st, 0, sizeof(*st));
    st->target_dbfs = cfg->target_dbfs;
    *out = st;
    return ESP_OK;
}

esp_err_t dsp_afe_agc_process(dsp_afe_agc_t *st, float *hop, bool speech, float *gain_db)
{
    if (st == NULL || hop == NULL) { return ESP_ERR_INVALID_ARG; }
    (void)speech;
    if (gain_db != NULL) { *gain_db = 0.0f; }
    return ESP_OK;
}

void dsp_afe_agc_set_target(dsp_afe_agc_t *st, float target_dbfs)
{
    st->target_dbfs = target_dbfs;
}
