#include <math.h>
#include <string.h>

#include "dsp_spec/fft.h"
#include "dsp_spec/spk_fbank.h"
#include "gen_grid.h"
#include "spec_internal.h"

#define PCM_FULL_SCALE 32768.0f
#define HAMMING_ALPHA 0.54 // scipy's general_hamming, the rest 1 - alpha
#define HTK_MEL_SCALE 2595.0
#define HTK_MEL_BREAK_HZ 700.0
#define WEIGHTS_PER_BIN 2 // neighbouring triangles overlap by half

struct dsp_spec_spk_fbank_s {
    dsp_spec_spk_fbank_config_t cfg;
    size_t n_frames;
    size_t n_bins; // fft_size / 2: the Nyquist bin is left out
    dsp_spec_fft_t *fft;
    float *window;
    uint16_t first_bin[DSP_SPEC_SPK_FBANK_MAX_BANDS];
    uint16_t n_weights[DSP_SPEC_SPK_FBANK_MAX_BANDS];
    float *weights; // band after band, from each band's first bin
    float *span;    // the frame's pre-emphasised samples, 0 off the window
    float *frame;
    dsp_spec_cplx_t *bins;
    float *power;
};

typedef struct {
    const int16_t *pcm;
    float mean, denom, preemphasis;
    long next; // prior is the normalised sample ahead of this one
    float prior;
} emphasis_t;

static bool config_ok(const dsp_spec_spk_fbank_config_t *c)
{
    return c != NULL && spec_fft_points_ok(c->fft_size) && c->frame_samples >= 2 &&
           c->frame_samples <= c->fft_size && c->hop_samples >= 1 && c->window_samples >= 2 &&
           c->window_samples + 2u * (c->hop_samples / 2u) >= c->frame_samples && c->n_bands >= 1 &&
           c->n_bands <= DSP_SPEC_SPK_FBANK_MAX_BANDS && c->f_min_hz >= 0.0f && c->f_min_hz < c->f_max_hz &&
           c->f_max_hz <= GEN_GRID_SAMPLE_RATE_HZ / 2.0f && c->preemphasis >= 0.0f && c->preemphasis < 1.0f &&
           c->eps > 0.0f;
}

size_t dsp_spec_spk_fbank_frames(const dsp_spec_spk_fbank_config_t *cfg)
{
    if (!config_ok(cfg)) { return 0; }
    const size_t padded = cfg->window_samples + 2u * (cfg->hop_samples / 2u);
    return 1 + (padded - cfg->frame_samples) / cfg->hop_samples;
}

size_t dsp_spec_spk_fbank_workspace_bytes(const dsp_spec_spk_fbank_config_t *cfg)
{
    if (!config_ok(cfg)) { return 0; }
    const size_t n_bins = cfg->fft_size / 2u;
    return SPEC_ALIGN_BYTES + spec_round_up(sizeof(struct dsp_spec_spk_fbank_s)) +
           spec_round_up(dsp_spec_fft_workspace_bytes(cfg->fft_size)) +
           2 * spec_round_up(cfg->frame_samples * sizeof(float)) +
           spec_round_up(WEIGHTS_PER_BIN * n_bins * sizeof(float)) +
           spec_round_up(cfg->fft_size * sizeof(float)) +
           spec_round_up((n_bins + 1) * sizeof(dsp_spec_cplx_t)) + spec_round_up(n_bins * sizeof(float));
}

// numpy.linspace in double, as srpipe.dsp.spec.spk_fbank takes it: the last point is exactly stop.
static double linspace_at(double start, double stop, size_t num, size_t i)
{
    return i == num - 1 ? stop : (double)i * ((stop - start) / (double)(num - 1)) + start;
}

static double htk_mel(double hz)
{
    return HTK_MEL_SCALE * log10(1.0 + hz / HTK_MEL_BREAK_HZ);
}

static void build_window(dsp_spec_spk_fbank_t *fb)
{
    const size_t n = fb->cfg.frame_samples;
    for (size_t i = 0; i < n; i++) {
        fb->window[i] = (float)(HAMMING_ALPHA + (1.0 - HAMMING_ALPHA) * cos(linspace_at(-M_PI, M_PI, n, i)));
    }
}

// Bin k sits at the mel of k * nyquist / (n_bins - 1) Hz, TensorFlow's spacing (KEHOACH 3.17); bin 0 weighs
// nothing.
static esp_err_t build_weights(dsp_spec_spk_fbank_t *fb)
{
    const dsp_spec_spk_fbank_config_t *c = &fb->cfg;
    const size_t points = (size_t)c->n_bands + 2;
    const double lo = htk_mel(c->f_min_hz);
    const double hi = htk_mel(c->f_max_hz);
    // The frame buffer is free until the first run: each bin's mel goes there, one log10 a bin.
    double *bin_mel = (double *)(void *)fb->frame;
    for (size_t k = 1; k < fb->n_bins; k++) {
        bin_mel[k] = htk_mel(linspace_at(0.0, GEN_GRID_SAMPLE_RATE_HZ / 2, fb->n_bins, k));
    }
    size_t used = 0;
    for (size_t b = 0; b < c->n_bands; b++) {
        const double lower = linspace_at(lo, hi, points, b);
        const double centre = linspace_at(lo, hi, points, b + 1);
        const double upper = linspace_at(lo, hi, points, b + 2);
        fb->first_bin[b] = 0;
        fb->n_weights[b] = 0;
        for (size_t k = 1; k < fb->n_bins; k++) {
            const double mel = bin_mel[k];
            const double rising = (mel - lower) / (centre - lower);
            const double falling = (upper - mel) / (upper - centre);
            const double w = fmax(0.0, fmin(rising, falling));
            if (w <= 0.0) { continue; }
            if (fb->n_weights[b] == 0) { fb->first_bin[b] = (uint16_t)k; }
            if (used == WEIGHTS_PER_BIN * fb->n_bins) { return ESP_ERR_INVALID_SIZE; }
            fb->weights[used++] = (float)w;
            fb->n_weights[b]++;
        }
    }
    return ESP_OK;
}

esp_err_t dsp_spec_spk_fbank_init(dsp_spec_spk_fbank_t **out, const dsp_spec_spk_fbank_config_t *cfg,
                                  void *mem, size_t bytes)
{
    const size_t need = dsp_spec_spk_fbank_workspace_bytes(cfg);
    if (out == NULL || mem == NULL || need == 0) { return ESP_ERR_INVALID_ARG; }
    if (bytes < need) { return ESP_ERR_INVALID_SIZE; }
    const size_t n_bins = cfg->fft_size / 2u;
    const size_t fft_bytes = dsp_spec_fft_workspace_bytes(cfg->fft_size);
    spec_carver_t c = spec_carver(mem, bytes);
    dsp_spec_spk_fbank_t *fb = spec_carve(&c, sizeof(*fb));
    memset(fb, 0, sizeof(*fb));
    fb->cfg = *cfg;
    fb->n_frames = dsp_spec_spk_fbank_frames(cfg);
    fb->n_bins = n_bins;
    void *fft_mem = spec_carve(&c, fft_bytes);
    fb->window = spec_carve(&c, cfg->frame_samples * sizeof(float));
    fb->weights = spec_carve(&c, WEIGHTS_PER_BIN * n_bins * sizeof(float));
    fb->span = spec_carve(&c, cfg->frame_samples * sizeof(float));
    fb->frame = spec_carve(&c, cfg->fft_size * sizeof(float));
    fb->bins = spec_carve(&c, (n_bins + 1) * sizeof(dsp_spec_cplx_t));
    fb->power = spec_carve(&c, n_bins * sizeof(float));
    if (fb->power == NULL) { return ESP_ERR_INVALID_SIZE; }
    esp_err_t err = dsp_spec_fft_init(&fb->fft, cfg->fft_size, fft_mem, fft_bytes);
    if (err == ESP_OK) { err = build_weights(fb); }
    if (err != ESP_OK) { return err; }
    build_window(fb);
    *out = fb;
    return ESP_OK;
}

static float normalised(const emphasis_t *e, size_t j)
{
    return ((float)e->pcm[j] / PCM_FULL_SCALE - e->mean) / e->denom;
}

// Sample j less the coefficient times sample j - 1; sample 0 reads sample 1, the reflected pad.
static float emphasised(emphasis_t *e, long j)
{
    if (j != e->next) { e->prior = normalised(e, j == 0 ? 1 : (size_t)j - 1); }
    const float x = normalised(e, (size_t)j);
    const float y = x - e->preemphasis * e->prior;
    e->prior = x;
    e->next = j + 1;
    return y;
}

// The span moves on a hop and emphasises only the samples new to it, so each sample is divided once.
static void fill_frame(dsp_spec_spk_fbank_t *fb, emphasis_t *e, size_t t)
{
    const dsp_spec_spk_fbank_config_t *c = &fb->cfg;
    const long start = (long)(t * c->hop_samples) - (long)(c->hop_samples / 2u);
    const size_t kept = t > 0 && c->hop_samples < c->frame_samples ? c->frame_samples - c->hop_samples : 0;
    memmove(fb->span, fb->span + (c->frame_samples - kept), kept * sizeof(float));
    for (size_t n = kept; n < c->frame_samples; n++) {
        const long j = start + (long)n;
        fb->span[n] = j < 0 || j >= (long)c->window_samples ? 0.0f : emphasised(e, j);
    }
    for (size_t n = 0; n < c->frame_samples; n++) {
        fb->frame[n] = fb->span[n] * fb->window[n];
    }
    memset(fb->frame + c->frame_samples, 0, (c->fft_size - c->frame_samples) * sizeof(float));
}

static float clipped(float x, float lo, float hi)
{
    return x < lo ? lo : (x > hi ? hi : x);
}

static void log_bands(dsp_spec_spk_fbank_t *fb, float *out)
{
    const float lo = fb->cfg.eps;
    const float hi = 1.0f / fb->cfg.eps;
    for (size_t k = 0; k < fb->n_bins; k++) {
        const dsp_spec_cplx_t b = fb->bins[k];
        fb->power[k] = clipped(b.re * b.re + b.im * b.im, lo, hi);
    }
    const float *w = fb->weights;
    for (size_t b = 0; b < fb->cfg.n_bands; b++) {
        float energy = 0.0f;
        const float *p = fb->power + fb->first_bin[b];
        for (size_t i = 0; i < fb->n_weights[b]; i++) {
            energy += w[i] * p[i];
        }
        w += fb->n_weights[b];
        out[b] = spec_ln_f32(clipped(energy, lo, hi) + fb->cfg.eps);
    }
}

esp_err_t dsp_spec_spk_fbank_run(dsp_spec_spk_fbank_t *fb, const int16_t *pcm, float *features)
{
    if (fb == NULL || pcm == NULL || features == NULL) { return ESP_ERR_INVALID_ARG; }
    const size_t n = fb->cfg.window_samples;
    // An integer sum of int16 is exact, so it is the double sum of the samples to the bit (KEHOACH 3.17).
    int64_t total = 0;
    for (size_t i = 0; i < n; i++) {
        total += pcm[i];
    }
    const double mean = (double)total / PCM_FULL_SCALE / (double)n;
    // Summed in double: in float32 the variance of 24000 samples drifts off torch's (KEHOACH 3.17).
    double squares = 0.0;
    for (size_t i = 0; i < n; i++) {
        const double d = (double)((float)pcm[i] / PCM_FULL_SCALE) - mean;
        squares += d * d;
    }
    const float denom = sqrtf((float)(squares / (double)n)) + fb->cfg.eps;
    emphasis_t e = {
        .pcm = pcm, .mean = (float)mean, .denom = denom, .preemphasis = fb->cfg.preemphasis, .next = -1};
    const size_t bands = fb->cfg.n_bands;
    for (size_t t = 0; t < fb->n_frames; t++) {
        fill_frame(fb, &e, t);
        const esp_err_t err = dsp_spec_fft_forward(fb->fft, fb->frame, fb->bins);
        if (err != ESP_OK) { return err; }
        log_bands(fb, features + t * bands);
    }
    for (size_t b = 0; b < bands; b++) {
        float sum = 0.0f;
        for (size_t t = 0; t < fb->n_frames; t++) {
            sum += features[t * bands + b];
        }
        const float time_mean = sum / (float)fb->n_frames;
        for (size_t t = 0; t < fb->n_frames; t++) {
            features[t * bands + b] -= time_mean;
        }
    }
    return ESP_OK;
}
