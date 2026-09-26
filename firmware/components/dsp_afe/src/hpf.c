#include "dsp_afe/hpf.h"

#include <math.h>
#include <string.h>

#include "afe_internal.h"
#include "dsps_biquad.h"
#include "dsps_biquad_gen.h"
#include "gen_array.h"
#include "gen_grid.h"

#define HPF_MIN_CUTOFF_HZ 10.0f
#define HPF_MAX_CUTOFF_HZ (GEN_GRID_SAMPLE_RATE_HZ / 4.0f)
#define BUTTERWORTH_Q ((float)M_SQRT1_2)
#define BIQUAD_COEFS 5 // b0 b1 b2 a1 a2 over a0, the layout of dsps_biquad_f32
#define BIQUAD_STATES 2

struct dsp_afe_hpf_s {
    float coef[BIQUAD_COEFS];
    float state[GEN_ARRAY_N_MICS][BIQUAD_STATES];
    uint8_t n_channels;
};

static bool config_ok(const dsp_afe_hpf_config_t *cfg)
{
    return cfg != NULL && cfg->cutoff_hz >= HPF_MIN_CUTOFF_HZ && cfg->cutoff_hz <= HPF_MAX_CUTOFF_HZ &&
           cfg->n_channels >= 1 && cfg->n_channels <= GEN_ARRAY_N_MICS;
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
    const esp_err_t err =
        dsps_biquad_gen_hpf_f32(st->coef, cfg->cutoff_hz / (float)GEN_GRID_SAMPLE_RATE_HZ, BUTTERWORTH_Q);
    if (err != ESP_OK) { return err; }
    *out = st;
    return ESP_OK;
}

esp_err_t dsp_afe_hpf_process(dsp_afe_hpf_t *st, uint8_t channel, float *samples, size_t n)
{
    if (st == NULL || channel >= st->n_channels || (samples == NULL && n > 0)) { return ESP_ERR_INVALID_ARG; }
    if (n == 0) { return ESP_OK; }
    return dsps_biquad_f32(samples, samples, (int)n, st->coef, st->state[channel]);
}
