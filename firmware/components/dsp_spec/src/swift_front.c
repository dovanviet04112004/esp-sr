#include <string.h>

#include "dsp_spec/fft.h"
#include "dsp_spec/swift_front.h"
#include "gen_grid.h"
#include "spec_internal.h"

#define PCM_FULL_SCALE 32768.0f
#define LOG_FLOOR 1e-8f
#define HISTORY_POINTS 2048 // the longest FFT's frame, centred DELAY_HOPS back

_Static_assert(HISTORY_POINTS % GEN_GRID_HOP_SAMPLES == 0, "a hop never wraps the history");

static const size_t k_points[DSP_SPEC_SWIFT_FRONT_SPECTRA] = {1024, 2048, 512};

struct dsp_spec_swift_front_s {
    dsp_spec_swift_front_tables_t tables[DSP_SPEC_SWIFT_FRONT_SPECTRA];
    dsp_spec_fft_t *fft[DSP_SPEC_SWIFT_FRONT_SPECTRA];
    float *history; // each sample twice, HISTORY_POINTS apart
    size_t oldest;  // the last HISTORY_POINTS run on from here
    float *magnitude;
    uint32_t pushed; // hops taken, held once frames come out
};

size_t dsp_spec_swift_front_workspace_bytes(void)
{
    size_t bytes = SPEC_ALIGN_BYTES + spec_round_up(sizeof(struct dsp_spec_swift_front_s));
    for (size_t s = 0; s < DSP_SPEC_SWIFT_FRONT_SPECTRA; s++) {
        bytes += spec_round_up(dsp_spec_fft_workspace_bytes(k_points[s]));
    }
    return bytes + spec_round_up(2 * HISTORY_POINTS * sizeof(float)) +
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
    f->history = spec_carve(&c, 2 * HISTORY_POINTS * sizeof(float));
    f->magnitude = spec_carve(&c, (HISTORY_POINTS / 2 + 1) * sizeof(float));
    if (f->magnitude == NULL) { return ESP_ERR_INVALID_SIZE; }
    dsp_spec_swift_front_reset(f);
    *out = f;
    return ESP_OK;
}

void dsp_spec_swift_front_reset(dsp_spec_swift_front_t *front)
{
    if (front == NULL) { return; }
    memset(front->history, 0, 2 * HISTORY_POINTS * sizeof(float));
    front->oldest = 0;
    front->pushed = 0;
}

static esp_err_t spectrum_logs(dsp_spec_swift_front_t *f, size_t s, float *out)
{
    const size_t n = k_points[s];
    const dsp_spec_swift_front_tables_t *t = &f->tables[s];
    const float *from = f->history + f->oldest + (HISTORY_POINTS - n) / 2;
    const esp_err_t err = spec_fft_magnitudes(f->fft[s], from, t->window, f->magnitude);
    if (err != ESP_OK) { return err; }
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
    float *slot = front->history + front->oldest;
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        slot[i] = (float)hop[i] / PCM_FULL_SCALE;
        slot[i + HISTORY_POINTS] = slot[i];
    }
    front->oldest = (front->oldest + GEN_GRID_HOP_SAMPLES) % HISTORY_POINTS;
    if (front->pushed <= DSP_SPEC_SWIFT_FRONT_DELAY_HOPS) { front->pushed++; }
    *ready = front->pushed > DSP_SPEC_SWIFT_FRONT_DELAY_HOPS;
    if (!*ready) { return ESP_OK; }
    for (size_t s = 0; s < DSP_SPEC_SWIFT_FRONT_SPECTRA; s++) {
        const esp_err_t err = spectrum_logs(front, s, out + s * DSP_SPEC_SWIFT_FRONT_BANDS);
        if (err != ESP_OK) { return err; }
    }
    return ESP_OK;
}
