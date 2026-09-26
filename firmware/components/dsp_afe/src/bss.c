#include "dsp_afe/bss.h"

#include <string.h>

#include "afe_internal.h"
#include "gen_grid.h"

// Neutral shell of E3-T4: each output copies one microphone until E8-T3 (KEHOACH 3.8).

#define ANGLE_UNKNOWN_DEG (-1)

struct dsp_afe_bss_s {
    float forget_tau_s;
};

static bool config_ok(const dsp_afe_bss_config_t *cfg)
{
    return cfg != NULL && cfg->forget_tau_s > 0.0f && cfg->spacing_m > 0.0f && cfg->speed_of_sound_m_s > 0.0f;
}

size_t dsp_afe_bss_workspace_bytes(const dsp_afe_bss_config_t *cfg)
{
    return config_ok(cfg) ? afe_region_bytes(sizeof(struct dsp_afe_bss_s)) : 0;
}

esp_err_t dsp_afe_bss_init(dsp_afe_bss_t **out, const dsp_afe_bss_config_t *cfg, void *mem, size_t bytes)
{
    if (out == NULL || mem == NULL || !config_ok(cfg)) { return ESP_ERR_INVALID_ARG; }
    dsp_afe_bss_t *st = afe_state(mem, bytes, sizeof(*st));
    if (st == NULL) { return ESP_ERR_INVALID_SIZE; }
    memset(st, 0, sizeof(*st));
    st->forget_tau_s = cfg->forget_tau_s;
    *out = st;
    return ESP_OK;
}

esp_err_t dsp_afe_bss_process(dsp_afe_bss_t *st, const dsp_spec_cplx_t *x0, const dsp_spec_cplx_t *x1,
                              dsp_spec_cplx_t *y0, dsp_spec_cplx_t *y1)
{
    if (st == NULL || x0 == NULL || x1 == NULL || y0 == NULL || y1 == NULL) { return ESP_ERR_INVALID_ARG; }
    memcpy(y0, x0, GEN_GRID_N_BINS * sizeof(*y0));
    memcpy(y1, x1, GEN_GRID_N_BINS * sizeof(*y1));
    return ESP_OK;
}

void dsp_afe_bss_directions(const dsp_afe_bss_t *st, int16_t angle_deg[2])
{
    (void)st;
    angle_deg[0] = ANGLE_UNKNOWN_DEG;
    angle_deg[1] = ANGLE_UNKNOWN_DEG;
}
