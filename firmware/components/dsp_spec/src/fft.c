#include <math.h>

#include "dsp_spec/fft.h"
#include "spec_internal.h"

struct dsp_spec_fft_s {
    size_t n_points;
    float *cosines;     // cos(2 pi k / n_points) for k < 3 n_points / 4
    float *sines;       // sin(2 pi k / n_points) for k < 3 n_points / 4
    uint16_t *reversed; // bit reversal of the n_points / 2 complex indices
    float *zr;          // the n_points / 2 complex points in transform
    float *zi;
};

size_t dsp_spec_fft_workspace_bytes(size_t n_points)
{
    if (!spec_fft_points_ok(n_points)) { return 0; }
    const size_t half = n_points / 2;
    const size_t twiddles = 3 * n_points / 4;
    return SPEC_ALIGN_BYTES + spec_round_up(sizeof(struct dsp_spec_fft_s)) +
           2 * spec_round_up(twiddles * sizeof(float)) + 2 * spec_round_up(half * sizeof(float)) +
           spec_round_up(half * sizeof(uint16_t));
}

esp_err_t dsp_spec_fft_init(dsp_spec_fft_t **out, size_t n_points, void *mem, size_t bytes)
{
    const size_t need = dsp_spec_fft_workspace_bytes(n_points);
    if (out == NULL || mem == NULL || need == 0) { return ESP_ERR_INVALID_ARG; }
    if (bytes < need) { return ESP_ERR_INVALID_SIZE; }
    const size_t half = n_points / 2;
    const size_t twiddles = 3 * n_points / 4;
    spec_carver_t c = spec_carver(mem, bytes);
    dsp_spec_fft_t *fft = spec_carve(&c, sizeof(*fft));
    fft->n_points = n_points;
    fft->cosines = spec_carve(&c, twiddles * sizeof(float));
    fft->sines = spec_carve(&c, twiddles * sizeof(float));
    fft->zr = spec_carve(&c, half * sizeof(float));
    fft->zi = spec_carve(&c, half * sizeof(float));
    fft->reversed = spec_carve(&c, half * sizeof(uint16_t));
    for (size_t k = 0; k < twiddles; k++) {
        const double angle = 2.0 * M_PI * (double)k / (double)n_points;
        fft->cosines[k] = (float)cos(angle);
        fft->sines[k] = (float)sin(angle);
    }
    unsigned bits = 0;
    while (((size_t)1 << bits) < half) {
        bits++;
    }
    for (size_t k = 0; k < half; k++) {
        size_t r = 0;
        for (unsigned b = 0; b < bits; b++) {
            r |= ((k >> b) & 1u) << (bits - 1 - b);
        }
        fft->reversed[k] = (uint16_t)r;
    }
    *out = fft;
    return ESP_OK;
}

size_t spec_fft_points(const dsp_spec_fft_t *fft)
{
    return fft->n_points;
}

// The radix-4 combine of points a, a + h, a + 2h, a + 3h, the last three already turned by their twiddles.
static inline void combine(float *zr, float *zi, size_t a, size_t h, float qr, float qi, float pr, float pi,
                           float rr, float ri)
{
    const float ar = zr[a];
    const float ai = zi[a];
    const float t0r = ar + qr;
    const float t0i = ai + qi;
    const float t1r = ar - qr;
    const float t1i = ai - qi;
    const float t2r = pr + rr;
    const float t2i = pi + ri;
    const float t3r = pr - rr;
    const float t3i = pi - ri;
    zr[a] = t0r + t2r;
    zi[a] = t0i + t2i;
    zr[a + h] = t1r + t3i;
    zi[a + h] = t1i - t3r;
    zr[a + 2 * h] = t0r - t2r;
    zi[a + 2 * h] = t0i - t2i;
    zr[a + 3 * h] = t1r - t3i;
    zi[a + 3 * h] = t1i + t3r;
}

// Decimation in time over zr, zi in bit-reversed order: radix-4 passes, one radix-2 stage first for odd log2.
static void butterflies(const dsp_spec_fft_t *fft)
{
    const size_t m = fft->n_points / 2;
    float *zr = fft->zr;
    float *zi = fft->zi;
    unsigned stages = 0;
    while (((size_t)1 << stages) < m) {
        stages++;
    }
    size_t h = 1;
    if (stages % 2) {
        for (size_t a = 0; a < m; a += 2) {
            const float ar = zr[a], ai = zi[a], br = zr[a + 1], bi = zi[a + 1];
            zr[a] = ar + br;
            zi[a] = ai + bi;
            zr[a + 1] = ar - br;
            zi[a + 1] = ai - bi;
        }
        h = 2;
    }
    for (; 4 * h <= m; h *= 4) {
        const size_t span = 4 * h;
        const size_t step = fft->n_points / span;
        for (size_t a = 0; a < m; a += span) {
            combine(zr, zi, a, h, zr[a + h], zi[a + h], zr[a + 2 * h], zi[a + 2 * h], zr[a + 3 * h],
                    zi[a + 3 * h]);
        }
        for (size_t j = 1; j < h; j++) {
            const float c1 = fft->cosines[j * step], s1 = fft->sines[j * step];
            const float c2 = fft->cosines[2 * j * step], s2 = fft->sines[2 * j * step];
            const float c3 = fft->cosines[3 * j * step], s3 = fft->sines[3 * j * step];
            for (size_t a = j; a < m; a += span) {
                const float br = zr[a + h], bi = zi[a + h];
                const float cr = zr[a + 2 * h], ci = zi[a + 2 * h];
                const float dr = zr[a + 3 * h], di = zi[a + 3 * h];
                const float pr = c1 * cr + s1 * ci;
                const float pi = c1 * ci - s1 * cr;
                const float qr = c2 * br + s2 * bi;
                const float qi = c2 * bi - s2 * br;
                const float rr = c3 * dr + s3 * di;
                const float ri = c3 * di - s3 * dr;
                combine(zr, zi, a, h, qr, qi, pr, pi, rr, ri);
            }
        }
    }
}

esp_err_t dsp_spec_fft_forward(dsp_spec_fft_t *fft, const float *in, dsp_spec_cplx_t *out)
{
    if (fft == NULL || in == NULL || out == NULL) { return ESP_ERR_INVALID_ARG; }
    const size_t m = fft->n_points / 2;
    float *zr = fft->zr;
    float *zi = fft->zi;
    for (size_t k = 0; k < m; k++) {
        zr[fft->reversed[k]] = in[2 * k];
        zi[fft->reversed[k]] = in[2 * k + 1];
    }
    butterflies(fft);
    out[0] = (dsp_spec_cplx_t){zr[0] + zi[0], 0.0f};
    out[m] = (dsp_spec_cplx_t){zr[0] - zi[0], 0.0f};
    for (size_t k = 1; k <= m / 2; k++) {
        const size_t j = m - k;
        const float even_r = (zr[k] + zr[j]) * 0.5f;
        const float even_i = (zi[k] - zi[j]) * 0.5f;
        const float odd_r = (zi[k] + zi[j]) * 0.5f;
        const float odd_i = (zr[j] - zr[k]) * 0.5f;
        const float c = fft->cosines[k];
        const float s = fft->sines[k];
        const float qr = c * odd_r + s * odd_i;
        const float qi = c * odd_i - s * odd_r;
        out[k] = (dsp_spec_cplx_t){even_r + qr, even_i + qi};
        if (j != k) { out[j] = (dsp_spec_cplx_t){even_r - qr, qi - even_i}; }
    }
    return ESP_OK;
}

esp_err_t dsp_spec_fft_inverse(dsp_spec_fft_t *fft, const dsp_spec_cplx_t *in, float *out)
{
    if (fft == NULL || in == NULL || out == NULL) { return ESP_ERR_INVALID_ARG; }
    const size_t m = fft->n_points / 2;
    float *zr = fft->zr;
    float *zi = fft->zi;
    // The bins merge into n / 2 points, conjugated, so the forward passes give the inverse's conjugate.
    zr[0] = in[0].re + in[m].re;
    zi[0] = -(in[0].re - in[m].re);
    for (size_t k = 1; k <= m / 2; k++) {
        const size_t j = m - k;
        const float sum_r = in[k].re + in[j].re;
        const float sum_i = in[k].im - in[j].im;
        const float diff_r = in[k].re - in[j].re;
        const float diff_i = in[k].im + in[j].im;
        const float c = fft->cosines[k];
        const float s = fft->sines[k];
        const float pr = c * diff_r - s * diff_i;
        const float pi = s * diff_r + c * diff_i;
        zr[fft->reversed[k]] = sum_r - pi;
        zi[fft->reversed[k]] = -(sum_i + pr);
        if (j != k) {
            zr[fft->reversed[j]] = sum_r + pi;
            zi[fft->reversed[j]] = -(pr - sum_i);
        }
    }
    butterflies(fft);
    const float scale = 1.0f / (float)fft->n_points;
    for (size_t k = 0; k < m; k++) {
        out[2 * k] = zr[k] * scale;
        out[2 * k + 1] = -(zi[k] * scale);
    }
    return ESP_OK;
}
