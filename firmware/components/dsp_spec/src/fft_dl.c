#include <string.h>

#include "dl_rfft.h"
#include "dsp_spec/fft.h"
#include "esp_heap_caps.h"
#include "spec_internal.h"

#define TABLE_CAPS (MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)

struct dsp_spec_fft_s {
    size_t n_points;
    dl_fft_f32_t *plan;
    float *packed; // DC, Nyquist, then re, im of bins 1 .. n/2 - 1
};

size_t dsp_spec_fft_workspace_bytes(size_t n_points)
{
    if (!spec_fft_points_ok(n_points)) { return 0; }
    return SPEC_ALIGN_BYTES + spec_round_up(sizeof(struct dsp_spec_fft_s)) +
           spec_round_up(n_points * sizeof(float));
}

esp_err_t dsp_spec_fft_init(dsp_spec_fft_t **out, size_t n_points, void *mem, size_t bytes)
{
    const size_t need = dsp_spec_fft_workspace_bytes(n_points);
    if (out == NULL || mem == NULL || need == 0) { return ESP_ERR_INVALID_ARG; }
    if (bytes < need) { return ESP_ERR_INVALID_SIZE; }
    spec_carver_t c = spec_carver(mem, bytes);
    dsp_spec_fft_t *fft = spec_carve(&c, sizeof(*fft));
    fft->n_points = n_points;
    fft->packed = spec_carve(&c, n_points * sizeof(float));
    fft->plan = dl_rfft_f32_init((int)n_points, TABLE_CAPS);
    if (fft->plan == NULL) { return ESP_ERR_NO_MEM; }
    *out = fft;
    return ESP_OK;
}

size_t spec_fft_points(const dsp_spec_fft_t *fft)
{
    return fft->n_points;
}

esp_err_t dsp_spec_fft_forward(dsp_spec_fft_t *fft, const float *in, dsp_spec_cplx_t *out)
{
    if (fft == NULL || in == NULL || out == NULL) { return ESP_ERR_INVALID_ARG; }
    const size_t half = fft->n_points / 2;
    memcpy(fft->packed, in, fft->n_points * sizeof(float));
    if (dl_rfft_f32_run(fft->plan, fft->packed) != ESP_OK) { return ESP_FAIL; }
    out[0] = (dsp_spec_cplx_t){fft->packed[0], 0.0f};
    out[half] = (dsp_spec_cplx_t){fft->packed[1], 0.0f};
    for (size_t k = 1; k < half; k++) {
        out[k] = (dsp_spec_cplx_t){fft->packed[2 * k], fft->packed[2 * k + 1]};
    }
    return ESP_OK;
}

esp_err_t dsp_spec_fft_inverse(dsp_spec_fft_t *fft, const dsp_spec_cplx_t *in, float *out)
{
    if (fft == NULL || in == NULL || out == NULL) { return ESP_ERR_INVALID_ARG; }
    const size_t half = fft->n_points / 2;
    fft->packed[0] = in[0].re;
    fft->packed[1] = in[half].re;
    for (size_t k = 1; k < half; k++) {
        fft->packed[2 * k] = in[k].re;
        fft->packed[2 * k + 1] = in[k].im;
    }
    if (dl_irfft_f32_run(fft->plan, fft->packed) != ESP_OK) { return ESP_FAIL; }
    memcpy(out, fft->packed, fft->n_points * sizeof(float));
    return ESP_OK;
}
