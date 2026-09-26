#include <math.h>
#include <string.h>

#include "dsp_spec/fft.h"
#include "dsps_fft2r.h"
#include "spec_internal.h"

#define TABLE_COMPLEX_POINTS                                                                                 \
    (SPEC_MAX_FFT_POINTS / 2) // esp-dsp keeps one global table sized for the largest FFT

struct dsp_spec_fft_s {
    size_t n_points;
    dsp_spec_cplx_t *z;       // n / 2 complex points
    dsp_spec_cplx_t *twiddle; // e^(-j 2 pi k / n) for k = 0 .. n / 2
};

size_t dsp_spec_fft_workspace_bytes(size_t n_points)
{
    if (!spec_fft_points_ok(n_points)) { return 0; }
    return SPEC_ALIGN_BYTES + spec_round_up(sizeof(struct dsp_spec_fft_s)) +
           spec_round_up(n_points / 2 * sizeof(dsp_spec_cplx_t)) +
           spec_round_up((n_points / 2 + 1) * sizeof(dsp_spec_cplx_t));
}

esp_err_t dsp_spec_fft_init(dsp_spec_fft_t **out, size_t n_points, void *mem, size_t bytes)
{
    const size_t need = dsp_spec_fft_workspace_bytes(n_points);
    if (out == NULL || mem == NULL || need == 0) { return ESP_ERR_INVALID_ARG; }
    if (bytes < need) { return ESP_ERR_INVALID_SIZE; }
    if (dsps_fft2r_init_fc32(NULL, TABLE_COMPLEX_POINTS) != ESP_OK) { return ESP_ERR_NO_MEM; }
    spec_carver_t c = spec_carver(mem, bytes);
    dsp_spec_fft_t *fft = spec_carve(&c, sizeof(*fft));
    fft->n_points = n_points;
    fft->z = spec_carve(&c, n_points / 2 * sizeof(dsp_spec_cplx_t));
    fft->twiddle = spec_carve(&c, (n_points / 2 + 1) * sizeof(dsp_spec_cplx_t));
    for (size_t k = 0; k <= n_points / 2; k++) {
        const double angle = -2.0 * M_PI * (double)k / (double)n_points;
        fft->twiddle[k] = (dsp_spec_cplx_t){(float)cos(angle), (float)sin(angle)};
    }
    *out = fft;
    return ESP_OK;
}

size_t spec_fft_points(const dsp_spec_fft_t *fft)
{
    return fft->n_points;
}

static dsp_spec_cplx_t mul(dsp_spec_cplx_t a, dsp_spec_cplx_t b)
{
    return (dsp_spec_cplx_t){a.re * b.re - a.im * b.im, a.re * b.im + a.im * b.re};
}

static void complex_fft(dsp_spec_fft_t *fft)
{
    const int m = (int)(fft->n_points / 2);
    dsps_fft2r_fc32((float *)fft->z, m);
    dsps_bit_rev_fc32((float *)fft->z, m);
}

// Even samples in re and odd in im give Z; X[k] = E[k] + W^k O[k] with E, O unpacked from Z and conj Z[m -
// k].
esp_err_t dsp_spec_fft_forward(dsp_spec_fft_t *fft, const float *in, dsp_spec_cplx_t *out)
{
    if (fft == NULL || in == NULL || out == NULL) { return ESP_ERR_INVALID_ARG; }
    const size_t m = fft->n_points / 2;
    memcpy(fft->z, in, fft->n_points * sizeof(float));
    complex_fft(fft);
    for (size_t k = 0; k <= m; k++) {
        const dsp_spec_cplx_t zk = fft->z[k % m];
        const dsp_spec_cplx_t zc = fft->z[(m - k) % m];
        const dsp_spec_cplx_t even = {0.5f * (zk.re + zc.re), 0.5f * (zk.im - zc.im)};
        const dsp_spec_cplx_t odd = {0.5f * (zk.im + zc.im), -0.5f * (zk.re - zc.re)};
        const dsp_spec_cplx_t turned = mul(fft->twiddle[k], odd);
        out[k] = (dsp_spec_cplx_t){even.re + turned.re, even.im + turned.im};
    }
    out[0].im = 0.0f;
    out[m].im = 0.0f;
    return ESP_OK;
}

// Rebuild Z[k] = E[k] + j O[k] from X, then take the inverse as conj(FFT(conj Z)) / m.
esp_err_t dsp_spec_fft_inverse(dsp_spec_fft_t *fft, const dsp_spec_cplx_t *in, float *out)
{
    if (fft == NULL || in == NULL || out == NULL) { return ESP_ERR_INVALID_ARG; }
    const size_t m = fft->n_points / 2;
    const float scale = 1.0f / (float)m;
    for (size_t k = 0; k < m; k++) {
        const dsp_spec_cplx_t xk = {in[k].re, k == 0 ? 0.0f : in[k].im};
        const dsp_spec_cplx_t xc = {in[m - k].re, k == 0 ? 0.0f : -in[m - k].im};
        const dsp_spec_cplx_t even = {0.5f * (xk.re + xc.re), 0.5f * (xk.im + xc.im)};
        const dsp_spec_cplx_t diff = {0.5f * (xk.re - xc.re), 0.5f * (xk.im - xc.im)};
        const dsp_spec_cplx_t unturn = {fft->twiddle[k].re, -fft->twiddle[k].im};
        const dsp_spec_cplx_t odd = mul(diff, unturn);
        fft->z[k] = (dsp_spec_cplx_t){even.re - odd.im, -(even.im + odd.re)};
    }
    complex_fft(fft);
    for (size_t n = 0; n < m; n++) {
        out[2 * n] = fft->z[n].re * scale;
        out[2 * n + 1] = -fft->z[n].im * scale;
    }
    return ESP_OK;
}
