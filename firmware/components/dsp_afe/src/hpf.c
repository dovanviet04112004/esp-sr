#include "dsp_afe/hpf.h"

#include <math.h>
#include <string.h>

#include "afe_internal.h"
#include "gen_array.h"
#include "gen_grid.h"

#define HPF_MIN_CUTOFF_HZ 10.0f
#define HPF_MAX_CUTOFF_HZ (GEN_GRID_SAMPLE_RATE_HZ / 4.0f)
#define BUTTERWORTH_Q ((float)M_SQRT1_2)

enum { B0, B1, B2, A1, A2, BIQUAD_COEFS }; // over a0

struct dsp_afe_hpf_s {
    float coef[BIQUAD_COEFS];
    float state[GEN_ARRAY_N_MICS][2];
    uint8_t n_channels;
};

static bool config_ok(const dsp_afe_hpf_config_t *cfg)
{
    return cfg != NULL && cfg->cutoff_hz >= HPF_MIN_CUTOFF_HZ && cfg->cutoff_hz <= HPF_MAX_CUTOFF_HZ &&
           cfg->n_channels >= 1 && cfg->n_channels <= GEN_ARRAY_N_MICS;
}

// Cosine and sine in double, rounded once, so newlib, glibc and srpipe give the same float (ADR-0004).
static void design(float cutoff_hz, float coef[BIQUAD_COEFS])
{
    const float w0 = 2.0f * (float)M_PI * cutoff_hz / (float)GEN_GRID_SAMPLE_RATE_HZ;
    const float c = (float)cos((double)w0);
    const float alpha = (float)sin((double)w0) / (2.0f * BUTTERWORTH_Q);
    const float a0 = 1.0f + alpha;
    coef[B0] = (1.0f + c) / 2.0f / a0;
    coef[B1] = -(1.0f + c) / a0;
    coef[B2] = coef[B0];
    coef[A1] = -2.0f * c / a0;
    coef[A2] = (1.0f - alpha) / a0;
}

size_t dsp_afe_hpf_workspace_bytes(const dsp_afe_hpf_config_t *cfg)
{
    return config_ok(cfg) ? afe_region_bytes(sizeof(struct dsp_afe_hpf_s)) : 0;
}

esp_err_t dsp_afe_hpf_init(dsp_afe_hpf_t **out, const dsp_afe_hpf_config_t *cfg, void *mem, size_t bytes)
{
    if (out == NULL || mem == NULL || !config_ok(cfg)) { return ESP_ERR_INVALID_ARG; }
    dsp_afe_hpf_t *st = afe_state(mem, bytes, sizeof(*st));
    if (st == NULL) { return ESP_ERR_INVALID_SIZE; }
    memset(st, 0, sizeof(*st));
    st->n_channels = cfg->n_channels;
    design(cfg->cutoff_hz, st->coef);
    *out = st;
    return ESP_OK;
}

esp_err_t dsp_afe_hpf_process(dsp_afe_hpf_t *st, uint8_t channel, float *samples, size_t n)
{
    if (st == NULL || channel >= st->n_channels || (samples == NULL && n > 0)) { return ESP_ERR_INVALID_ARG; }
    // Locals, not st->coef: samples could alias the coefficients, which would reload them every sample.
    const float b0 = st->coef[B0], b1 = st->coef[B1], b2 = st->coef[B2];
    const float a1 = st->coef[A1], a2 = st->coef[A2];
    float s0 = st->state[channel][0];
    float s1 = st->state[channel][1];
    for (size_t i = 0; i < n; i++) {
        const float x = samples[i];
        const float y = b0 * x + s0;
        s0 = b1 * x - a1 * y + s1;
        s1 = b2 * x - a2 * y;
        samples[i] = y;
    }
    st->state[channel][0] = s0;
    st->state[channel][1] = s1;
    return ESP_OK;
}

void dsp_afe_hpf_reset(dsp_afe_hpf_t *st)
{
    memset(st->state, 0, sizeof(st->state));
}
