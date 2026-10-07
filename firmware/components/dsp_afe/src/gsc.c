#include "dsp_afe/gsc.h"

#include <string.h>

#include "afe_internal.h"
#include "gen_afe.h"
#include "gen_grid.h"

#define DEG_PER_HALF_TURN 180.0

struct dsp_afe_gsc_s {
    float spacing_m;
    float speed_of_sound_m_s;
    float mu;
    float keep; // 1 - mu * leakage
    float cap;
    float cap_power;
    bool steered;
    float angle_deg;
    dsp_spec_cplx_t steer[GEN_GRID_N_BINS]; // exp(-j w_k tau) of angle_deg
    dsp_spec_cplx_t weight[GEN_GRID_N_BINS];
};

static bool config_ok(const dsp_afe_gsc_config_t *cfg)
{
    return cfg != NULL && cfg->spacing_m > 0.0f && cfg->speed_of_sound_m_s > 0.0f && cfg->step_size > 0.0f &&
           cfg->leakage >= 0.0f && cfg->weight_max > 0.0f && 1.0f - cfg->step_size * cfg->leakage > 0.0f;
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
    st->spacing_m = cfg->spacing_m;
    st->speed_of_sound_m_s = cfg->speed_of_sound_m_s;
    st->mu = cfg->step_size;
    st->keep = 1.0f - cfg->step_size * cfg->leakage;
    st->cap = cfg->weight_max;
    st->cap_power = cfg->weight_max * cfg->weight_max;
    *out = st;
    return ESP_OK;
}

static void steer_to(dsp_afe_gsc_t *st, float angle_deg)
{
    const double bin_hz = (double)GEN_GRID_SAMPLE_RATE_HZ / GEN_GRID_FFT_SIZE;
    const double theta_rad = (double)angle_deg * AFE_PI / DEG_PER_HALF_TURN;
    const double tau_s = (double)st->spacing_m * afe_cos_series(theta_rad) / (double)st->speed_of_sound_m_s;
    const double per_bin = 2.0 * AFE_PI * bin_hz * tau_s;
    const float turn_re = (float)afe_cos_series(per_bin);
    const float turn_im = (float)-afe_sin_series(per_bin);
    float c = 1.0f;
    float s = 0.0f;
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        st->steer[k] = (dsp_spec_cplx_t){c, s};
        const float next_c = c * turn_re - s * turn_im;
        s = c * turn_im + s * turn_re;
        c = next_c;
    }
    st->steered = true;
    st->angle_deg = angle_deg;
}

static void learn(dsp_afe_gsc_t *st, size_t k, float block_re, float block_im, float y_re, float y_im)
{
    const float gain =
        st->mu * afe_recip_f32(block_re * block_re + block_im * block_im + GEN_AFE_GSC_POWER_FLOOR);
    float w_re = st->keep * st->weight[k].re + gain * (block_re * y_re + block_im * y_im);
    float w_im = st->keep * st->weight[k].im + gain * (block_im * y_re - block_re * y_im);
    const float norm = w_re * w_re + w_im * w_im;
    if (norm > st->cap_power) {
        const float shrink = st->cap * afe_rsqrt_f32(norm);
        w_re = w_re * shrink;
        w_im = w_im * shrink;
    }
    st->weight[k] = (dsp_spec_cplx_t){w_re, w_im};
}

esp_err_t dsp_afe_gsc_process(dsp_afe_gsc_t *st, const dsp_spec_cplx_t *x0, const dsp_spec_cplx_t *x1,
                              float angle_deg, bool adapt, dsp_spec_cplx_t *out)
{
    if (st == NULL || x0 == NULL || x1 == NULL || out == NULL) { return ESP_ERR_INVALID_ARG; }
    if (!st->steered || angle_deg != st->angle_deg) { steer_to(st, angle_deg); }
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        const float aligned_re = x1[k].re * st->steer[k].re - x1[k].im * st->steer[k].im;
        const float aligned_im = x1[k].re * st->steer[k].im + x1[k].im * st->steer[k].re;
        const float beam_re = 0.5f * (x0[k].re + aligned_re);
        const float beam_im = 0.5f * (x0[k].im + aligned_im);
        const float block_re = x0[k].re - aligned_re;
        const float block_im = x0[k].im - aligned_im;
        const float w_re = st->weight[k].re;
        const float w_im = st->weight[k].im;
        const float y_re = beam_re - (w_re * block_re + w_im * block_im);
        const float y_im = beam_im - (w_re * block_im - w_im * block_re);
        out[k] = (dsp_spec_cplx_t){y_re, y_im};
        if (adapt) { learn(st, k, block_re, block_im, y_re, y_im); }
    }
    return ESP_OK;
}

void dsp_afe_gsc_reset(dsp_afe_gsc_t *st)
{
    memset(st->weight, 0, sizeof(st->weight));
}
