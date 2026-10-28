#include <math.h>
#include <string.h>

#include "dsp_spec/fft.h"
#include "dsp_spec/swift_front.h"
#include "gen_grid.h"
#include "spec_internal.h"

#define PCM_FULL_SCALE 32768.0f
#define LOG_FLOOR 1e-8f
#define HISTORY_POINTS 2048 // the longest FFT's frame, centred DELAY_HOPS back

static const size_t k_points[DSP_SPEC_SWIFT_FRONT_SPECTRA] = {1024, 2048, 512};

struct dsp_spec_swift_front_s {
    dsp_spec_swift_front_tables_t tables[DSP_SPEC_SWIFT_FRONT_SPECTRA];
    dsp_spec_fft_t *fft[DSP_SPEC_SWIFT_FRONT_SPECTRA];
    float *history; // the last HISTORY_POINTS samples, oldest first
    float *frame;
    dsp_spec_cplx_t *bins;
    float *magnitude;
    uint32_t pushed; // hops taken, held once frames come out
};

size_t dsp_spec_swift_front_workspace_bytes(void)
{
    size_t bytes = SPEC_ALIGN_BYTES + spec_round_up(sizeof(struct dsp_spec_swift_front_s));
    for (size_t s = 0; s < DSP_SPEC_SWIFT_FRONT_SPECTRA; s++) {
        bytes += spec_round_up(dsp_spec_fft_workspace_bytes(k_points[s]));
    }
    return bytes + 2 * spec_round_up(HISTORY_POINTS * sizeof(float)) +
           spec_round_up((HISTORY_POINTS / 2 + 1) * sizeof(dsp_spec_cplx_t)) +
           spec_round_up((HISTORY_POINTS / 2 + 1) * sizeof(float));
}

static bool bands_fit(const dsp_spec_swift_front_tables_t *t, size_t points)
{
    if (t->window == NULL || t->first == NULL || t->count == NULL || t->weights == NULL) { return false; }
    for (size_t b = 0; b < DSP_SPEC_SWIFT_FRONT_BANDS; b++) {
        if ((size_t)t->first[b] + t->count[b] > points / 2 + 1) { return false; }
    }
    return true;
}

esp_err_t dsp_spec_swift_front_init(dsp_spec_swift_front_t **out,
                                    const dsp_spec_swift_front_tables_t tables[DSP_SPEC_SWIFT_FRONT_SPECTRA],
                                    void *mem, size_t bytes)
{
    if (out == NULL || tables == NULL || mem == NULL) { return ESP_ERR_INVALID_ARG; }
    if (bytes < dsp_spec_swift_front_workspace_bytes()) { return ESP_ERR_INVALID_SIZE; }
    spec_carver_t c = spec_carver(mem, bytes);
    dsp_spec_swift_front_t *f = spec_carve(&c, sizeof(*f));
    memset(f, 0, sizeof(*f));
    for (size_t s = 0; s < DSP_SPEC_SWIFT_FRONT_SPECTRA; s++) {
        if (!bands_fit(&tables[s], k_points[s])) { return ESP_ERR_INVALID_SIZE; }
        f->tables[s] = tables[s];
        const size_t fft_bytes = dsp_spec_fft_workspace_bytes(k_points[s]);
        const esp_err_t err =
            dsp_spec_fft_init(&f->fft[s], k_points[s], spec_carve(&c, fft_bytes), fft_bytes);
        if (err != ESP_OK) { return err; }
    }
    f->history = spec_carve(&c, HISTORY_POINTS * sizeof(float));
    f->frame = spec_carve(&c, HISTORY_POINTS * sizeof(float));
    f->bins = spec_carve(&c, (HISTORY_POINTS / 2 + 1) * sizeof(dsp_spec_cplx_t));
    f->magnitude = spec_carve(&c, (HISTORY_POINTS / 2 + 1) * sizeof(float));
    if (f->magnitude == NULL) { return ESP_ERR_INVALID_SIZE; }
    dsp_spec_swift_front_reset(f);
    *out = f;
    return ESP_OK;
}

void dsp_spec_swift_front_reset(dsp_spec_swift_front_t *front)
{
    if (front == NULL) { return; }
    memset(front->history, 0, HISTORY_POINTS * sizeof(float));
    front->pushed = 0;
}

static esp_err_t spectrum_logs(dsp_spec_swift_front_t *f, size_t s, float *out)
{
    const size_t n = k_points[s];
    const dsp_spec_swift_front_tables_t *t = &f->tables[s];
    const float *from = f->history + (HISTORY_POINTS - n) / 2;
    for (size_t i = 0; i < n; i++) {
        f->frame[i] = from[i] * t->window[i];
    }
    const esp_err_t err = dsp_spec_fft_forward(f->fft[s], f->frame, f->bins);
    if (err != ESP_OK) { return err; }
    for (size_t k = 0; k <= n / 2; k++) {
        const dsp_spec_cplx_t b = f->bins[k];
        f->magnitude[k] = sqrtf(b.re * b.re + b.im * b.im);
    }
    const float *w = t->weights;
    for (size_t b = 0; b < DSP_SPEC_SWIFT_FRONT_BANDS; b++) {
        float acc = 0.0f;
        const float *m = f->magnitude + t->first[b];
        for (size_t i = 0; i < t->count[b]; i++) {
            acc += w[i] * m[i];
        }
        w += t->count[b];
        out[b] = spec_ln_f32(acc + LOG_FLOOR);
    }
    return ESP_OK;
}

esp_err_t dsp_spec_swift_front_push(dsp_spec_swift_front_t *front, const int16_t *hop, float *out,
                                    bool *ready)
{
    if (front == NULL || hop == NULL || out == NULL || ready == NULL) { return ESP_ERR_INVALID_ARG; }
    const size_t kept = HISTORY_POINTS - GEN_GRID_HOP_SAMPLES;
    memmove(front->history, front->history + GEN_GRID_HOP_SAMPLES, kept * sizeof(float));
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        front->history[kept + i] = (float)hop[i] / PCM_FULL_SCALE;
    }
    if (front->pushed <= DSP_SPEC_SWIFT_FRONT_DELAY_HOPS) { front->pushed++; }
    *ready = front->pushed > DSP_SPEC_SWIFT_FRONT_DELAY_HOPS;
    if (!*ready) { return ESP_OK; }
    for (size_t s = 0; s < DSP_SPEC_SWIFT_FRONT_SPECTRA; s++) {
        const esp_err_t err = spectrum_logs(front, s, out + s * DSP_SPEC_SWIFT_FRONT_BANDS);
        if (err != ESP_OK) { return err; }
    }
    return ESP_OK;
}
