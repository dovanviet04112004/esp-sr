#include <math.h>
#include <string.h>

#include "dsp_spec/mel.h"
#include "gen_grid.h"
#include "spec_internal.h"

#define SLANEY_LINEAR_HZ_PER_MEL (200.0 / 3.0)
#define SLANEY_KNEE_HZ 1000.0
#define SLANEY_KNEE_MEL (SLANEY_KNEE_HZ / SLANEY_LINEAR_HZ_PER_MEL)
#define MAX_WEIGHTS (2 * GEN_GRID_N_BINS) // a bin feeds at most two bands

struct dsp_spec_mel_s {
    uint16_t n_bands;
    float log_floor;
    uint16_t first_bin[DSP_SPEC_MEL_MAX_BANDS];
    uint16_t n_weights[DSP_SPEC_MEL_MAX_BANDS];
    float weights[MAX_WEIGHTS];
    float cos_table[4 * DSP_SPEC_MEL_MAX_BANDS]; // cos(pi m / (2 n_bands)), one period of the DCT-II kernel
};

static double hz_to_mel(double hz)
{
    if (hz < SLANEY_KNEE_HZ) { return hz / SLANEY_LINEAR_HZ_PER_MEL; }
    return SLANEY_KNEE_MEL + log(hz / SLANEY_KNEE_HZ) / (log(6.4) / 27.0);
}

static double mel_to_hz(double mel)
{
    if (mel < SLANEY_KNEE_MEL) { return mel * SLANEY_LINEAR_HZ_PER_MEL; }
    return SLANEY_KNEE_HZ * exp((log(6.4) / 27.0) * (mel - SLANEY_KNEE_MEL));
}

static bool config_ok(const dsp_spec_mel_config_t *cfg)
{
    return cfg != NULL && cfg->n_bands >= 1 && cfg->n_bands <= DSP_SPEC_MEL_MAX_BANDS &&
           cfg->f_min_hz >= 0.0f && cfg->f_min_hz < cfg->f_max_hz &&
           cfg->f_max_hz <= GEN_GRID_SAMPLE_RATE_HZ / 2.0f && cfg->log_floor > 0.0f;
}

size_t dsp_spec_mel_workspace_bytes(const dsp_spec_mel_config_t *cfg)
{
    return config_ok(cfg) ? SPEC_ALIGN_BYTES + spec_round_up(sizeof(struct dsp_spec_mel_s)) : 0;
}

// Edges follow numpy.linspace in mel, with the last edge exactly f_max, as srpipe.dsp.spec.mel does.
static double edge_hz(const dsp_spec_mel_config_t *cfg, size_t i)
{
    const double lo = hz_to_mel(cfg->f_min_hz);
    const double hi = hz_to_mel(cfg->f_max_hz);
    const size_t last = (size_t)cfg->n_bands + 1;
    return mel_to_hz(i == last ? hi : lo + (double)i * ((hi - lo) / (double)last));
}

static esp_err_t build_filters(dsp_spec_mel_t *mel, const dsp_spec_mel_config_t *cfg)
{
    size_t used = 0;
    for (size_t b = 0; b < cfg->n_bands; b++) {
        const double lower = edge_hz(cfg, b);
        const double centre = edge_hz(cfg, b + 1);
        const double upper = edge_hz(cfg, b + 2);
        const double area = 2.0 / (upper - lower);
        mel->first_bin[b] = 0;
        mel->n_weights[b] = 0;
        for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
            const double hz = (double)k * GEN_GRID_SAMPLE_RATE_HZ / GEN_GRID_FFT_SIZE;
            const double rising = (hz - lower) / (centre - lower);
            const double falling = (upper - hz) / (upper - centre);
            const double w = fmax(0.0, fmin(rising, falling)) * area;
            if (w <= 0.0) { continue; }
            if (mel->n_weights[b] == 0) { mel->first_bin[b] = (uint16_t)k; }
            if (used == MAX_WEIGHTS) { return ESP_ERR_INVALID_SIZE; }
            mel->weights[used++] = (float)w;
            mel->n_weights[b]++;
        }
    }
    return ESP_OK;
}

esp_err_t dsp_spec_mel_init(dsp_spec_mel_t **out, const dsp_spec_mel_config_t *cfg, void *mem, size_t bytes)
{
    const size_t need = dsp_spec_mel_workspace_bytes(cfg);
    if (out == NULL || mem == NULL || need == 0) { return ESP_ERR_INVALID_ARG; }
    if (bytes < need) { return ESP_ERR_INVALID_SIZE; }
    spec_carver_t c = spec_carver(mem, bytes);
    dsp_spec_mel_t *mel = spec_carve(&c, sizeof(*mel));
    memset(mel, 0, sizeof(*mel));
    mel->n_bands = cfg->n_bands;
    mel->log_floor = cfg->log_floor;
    const esp_err_t err = build_filters(mel, cfg);
    if (err != ESP_OK) { return err; }
    for (size_t m = 0; m < 4u * cfg->n_bands; m++) {
        mel->cos_table[m] = (float)cos(M_PI * (double)m / (2.0 * cfg->n_bands));
    }
    *out = mel;
    return ESP_OK;
}

esp_err_t dsp_spec_mel_log(const dsp_spec_mel_t *mel, const dsp_spec_cplx_t *bins, float *out)
{
    if (mel == NULL || bins == NULL || out == NULL) { return ESP_ERR_INVALID_ARG; }
    const float *w = mel->weights;
    for (size_t b = 0; b < mel->n_bands; b++) {
        float energy = 0.0f;
        const dsp_spec_cplx_t *x = bins + mel->first_bin[b];
        for (size_t i = 0; i < mel->n_weights[b]; i++) {
            energy += w[i] * (x[i].re * x[i].re + x[i].im * x[i].im);
        }
        w += mel->n_weights[b];
        out[b] = logf(energy + mel->log_floor);
    }
    return ESP_OK;
}

esp_err_t dsp_spec_mel_mfcc(const dsp_spec_mel_t *mel, const float *log_mel, float *ceps, size_t n_ceps)
{
    if (mel == NULL || log_mel == NULL || ceps == NULL || n_ceps == 0 || n_ceps > mel->n_bands) {
        return ESP_ERR_INVALID_ARG;
    }
    const size_t n = mel->n_bands;
    const float first = sqrtf(1.0f / (float)n);
    const float rest = sqrtf(2.0f / (float)n);
    for (size_t k = 0; k < n_ceps; k++) {
        float sum = 0.0f;
        for (size_t i = 0; i < n; i++) {
            sum += mel->cos_table[(k * (2 * i + 1)) % (4 * n)] * log_mel[i];
        }
        ceps[k] = (k == 0 ? first : rest) * sum;
    }
    return ESP_OK;
}
