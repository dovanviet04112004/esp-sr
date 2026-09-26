#include "dsp_afe/doa.h"

#include <string.h>

#include "afe_internal.h"
#include "gen_grid.h"

// Neutral shell of E3-T4: the angle stays unknown until E8-T1 writes the search (KEHOACH 3.6).

#define ANGLE_UNKNOWN_DEG (-1)

struct dsp_afe_doa_s {
    dsp_afe_doa_result_t last;
};

static bool config_ok(const dsp_afe_doa_config_t *cfg)
{
    return cfg != NULL && cfg->spacing_m > 0.0f && cfg->speed_of_sound_m_s > 0.0f &&
           cfg->band_min_hz >= 0.0f && cfg->band_min_hz < cfg->band_max_hz &&
           cfg->band_max_hz <= GEN_GRID_SAMPLE_RATE_HZ / 2.0f && cfg->grid_step_deg > 0.0f &&
           cfg->smooth_tau_s > 0.0f;
}

size_t dsp_afe_doa_workspace_bytes(const dsp_afe_doa_config_t *cfg)
{
    return config_ok(cfg) ? afe_region_bytes(sizeof(struct dsp_afe_doa_s)) : 0;
}

esp_err_t dsp_afe_doa_init(dsp_afe_doa_t **out, const dsp_afe_doa_config_t *cfg, void *mem, size_t bytes)
{
    if (out == NULL || mem == NULL || !config_ok(cfg)) { return ESP_ERR_INVALID_ARG; }
    dsp_afe_doa_t *st = afe_state(mem, bytes, sizeof(*st));
    if (st == NULL) { return ESP_ERR_INVALID_SIZE; }
    memset(st, 0, sizeof(*st));
    st->last.angle_deg = ANGLE_UNKNOWN_DEG;
    *out = st;
    return ESP_OK;
}

esp_err_t dsp_afe_doa_process(dsp_afe_doa_t *st, const dsp_spec_cplx_t *x0, const dsp_spec_cplx_t *x1,
                              bool update, dsp_afe_doa_result_t *out)
{
    if (st == NULL || x0 == NULL || x1 == NULL || out == NULL) { return ESP_ERR_INVALID_ARG; }
    (void)update;
    *out = st->last;
    return ESP_OK;
}
