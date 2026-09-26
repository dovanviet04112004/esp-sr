#include <string.h>

#include "dsp_spec/stft.h"
#include "dsp_spec/window.h"
#include "spec_internal.h"

struct dsp_spec_stft_s {
    dsp_spec_fft_t *fft;
    float window[GEN_GRID_FFT_SIZE];
    float buffer[GEN_GRID_FFT_SIZE];
    float frame[GEN_GRID_FFT_SIZE];
};

struct dsp_spec_istft_s {
    dsp_spec_fft_t *fft;
    float window[GEN_GRID_FFT_SIZE];
    float overlap[GEN_GRID_FFT_SIZE];
    float frame[GEN_GRID_FFT_SIZE];
};

size_t dsp_spec_stft_workspace_bytes(void)
{
    return SPEC_ALIGN_BYTES + spec_round_up(sizeof(struct dsp_spec_stft_s));
}

esp_err_t dsp_spec_stft_init(dsp_spec_stft_t **out, dsp_spec_fft_t *fft, void *mem, size_t bytes)
{
    if (out == NULL || fft == NULL || mem == NULL || spec_fft_points(fft) != GEN_GRID_FFT_SIZE) {
        return ESP_ERR_INVALID_ARG;
    }
    if (bytes < dsp_spec_stft_workspace_bytes()) { return ESP_ERR_INVALID_SIZE; }
    spec_carver_t c = spec_carver(mem, bytes);
    dsp_spec_stft_t *st = spec_carve(&c, sizeof(*st));
    st->fft = fft;
    dsp_spec_window_sqrt_hann(st->window, GEN_GRID_FFT_SIZE);
    dsp_spec_stft_reset(st);
    *out = st;
    return ESP_OK;
}

esp_err_t dsp_spec_stft_analyze(dsp_spec_stft_t *st, const float *hop, dsp_spec_cplx_t *bins)
{
    if (st == NULL || hop == NULL || bins == NULL) { return ESP_ERR_INVALID_ARG; }
    const size_t keep = GEN_GRID_FFT_SIZE - GEN_GRID_HOP_SAMPLES;
    memmove(st->buffer, st->buffer + GEN_GRID_HOP_SAMPLES, keep * sizeof(float));
    memcpy(st->buffer + keep, hop, GEN_GRID_HOP_SAMPLES * sizeof(float));
    for (size_t i = 0; i < GEN_GRID_FFT_SIZE; i++) {
        st->frame[i] = st->buffer[i] * st->window[i];
    }
    return dsp_spec_fft_forward(st->fft, st->frame, bins);
}

void dsp_spec_stft_reset(dsp_spec_stft_t *st)
{
    memset(st->buffer, 0, sizeof(st->buffer));
}

size_t dsp_spec_istft_workspace_bytes(void)
{
    return SPEC_ALIGN_BYTES + spec_round_up(sizeof(struct dsp_spec_istft_s));
}

esp_err_t dsp_spec_istft_init(dsp_spec_istft_t **out, dsp_spec_fft_t *fft, void *mem, size_t bytes)
{
    if (out == NULL || fft == NULL || mem == NULL || spec_fft_points(fft) != GEN_GRID_FFT_SIZE) {
        return ESP_ERR_INVALID_ARG;
    }
    if (bytes < dsp_spec_istft_workspace_bytes()) { return ESP_ERR_INVALID_SIZE; }
    spec_carver_t c = spec_carver(mem, bytes);
    dsp_spec_istft_t *st = spec_carve(&c, sizeof(*st));
    st->fft = fft;
    dsp_spec_window_sqrt_hann(st->window, GEN_GRID_FFT_SIZE);
    dsp_spec_istft_reset(st);
    *out = st;
    return ESP_OK;
}

esp_err_t dsp_spec_istft_synthesize(dsp_spec_istft_t *st, const dsp_spec_cplx_t *bins, float *hop)
{
    if (st == NULL || bins == NULL || hop == NULL) { return ESP_ERR_INVALID_ARG; }
    const esp_err_t err = dsp_spec_fft_inverse(st->fft, bins, st->frame);
    if (err != ESP_OK) { return err; }
    for (size_t i = 0; i < GEN_GRID_FFT_SIZE; i++) {
        st->overlap[i] += st->frame[i] * st->window[i];
    }
    const size_t keep = GEN_GRID_FFT_SIZE - GEN_GRID_HOP_SAMPLES;
    memcpy(hop, st->overlap, GEN_GRID_HOP_SAMPLES * sizeof(float));
    memmove(st->overlap, st->overlap + GEN_GRID_HOP_SAMPLES, keep * sizeof(float));
    memset(st->overlap + keep, 0, GEN_GRID_HOP_SAMPLES * sizeof(float));
    return ESP_OK;
}

void dsp_spec_istft_reset(dsp_spec_istft_t *st)
{
    memset(st->overlap, 0, sizeof(st->overlap));
}
