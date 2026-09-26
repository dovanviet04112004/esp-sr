#include "dsp_afe/gsc.h"

#include <string.h>

#include "afe_internal.h"
#include "gen_grid.h"

// Neutral shell of E3-T4: the output is the plain two-channel mean until E8-T2 (KEHOACH 3.7).

struct dsp_afe_gsc_s {
    float step_size;
};

static bool config_ok(const dsp_afe_gsc_config_t *cfg)
{
    return cfg != NULL && cfg->spacing_m > 0.0f && cfg->speed_of_sound_m_s > 0.0f && cfg->step_size > 0.0f &&
           cfg->leakage >= 0.0f && cfg->weight_max >= 0.0f;
}

size_t dsp_afe_gsc_workspace_bytes(const dsp_afe_gsc_config_t *cfg)
{
    return config_ok(cfg) ? afe_region_bytes(sizeof(struct dsp_afe_gsc_s)) : 0;
}

esp_err_t dsp_afe_gsc_init(dsp_afe_gsc_t **out, const dsp_afe_gsc_config_t *cfg, void *mem, size_t bytes)
{
    if (out == NULL || mem == NULL || !config_ok(cfg)) { return ESP_ERR_INVALID_ARG; }
    dsp_afe_gsc_t *st = afe_state(mem, bytes, sizeof(*st));
    if (st == NULL) { return ESP_ERR_INVALID_SIZE; }
    memset(st, 0, sizeof(*st));
    st->step_size = cfg->step_size;
    *out = st;
    return ESP_OK;
}

esp_err_t dsp_afe_gsc_process(dsp_afe_gsc_t *st, const dsp_spec_cplx_t *x0, const dsp_spec_cplx_t *x1,
                              float angle_deg, bool adapt, dsp_spec_cplx_t *out)
{
    if (st == NULL || x0 == NULL || x1 == NULL || out == NULL) { return ESP_ERR_INVALID_ARG; }
    (void)angle_deg;
    (void)adapt;
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        out[k] = (dsp_spec_cplx_t){0.5f * (x0[k].re + x1[k].re), 0.5f * (x0[k].im + x1[k].im)};
    }
    return ESP_OK;
}
