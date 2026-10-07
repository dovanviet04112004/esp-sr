#include "dsp_afe/doa.h"

#include <float.h>
#include <math.h>
#include <string.h>

#include "afe_internal.h"
#include "gen_array.h"
#include "gen_grid.h"

#define ANGLE_UNKNOWN_DEG (-1)
#define CONFIDENCE_MAX 255.0f
#define DEG_PER_HALF_TURN 180.0

typedef struct {
    size_t first; // band's first bin, DC left out
    size_t n_bins;
    size_t n_angles;
    size_t n_half; // angles searched; each other one is a mirror 180 - theta
} geometry_t;

struct dsp_afe_doa_s {
    geometry_t g;
    float step_deg;
    float keep;
    float take;
    dsp_spec_cplx_t *cross; // smoothed X0 conj(X1) over the band
    dsp_spec_cplx_t *start; // exp(j w_first tau) per searched angle
    dsp_spec_cplx_t *turn;  // exp(j w_1 tau) per searched angle
    float *unit_re;         // cross / |cross|, 0 where a bin has no power
    float *unit_im;
    float *response;
    dsp_afe_doa_result_t last;
};

static bool geometry_of(const dsp_afe_doa_config_t *cfg, geometry_t *g)
{
    if (cfg == NULL ||
        !(cfg->spacing_m > 0.0f && cfg->speed_of_sound_m_s > 0.0f && cfg->band_min_hz >= 0.0f &&
          cfg->band_min_hz < cfg->band_max_hz && cfg->band_max_hz <= GEN_GRID_SAMPLE_RATE_HZ / 2.0f &&
          cfg->grid_step_deg > 0.0f && cfg->smooth_tau_s > 0.0f)) {
        return false;
    }
    const float span = (float)(GEN_ARRAY_DOA_MAX_DEG - GEN_ARRAY_DOA_MIN_DEG);
    const long steps = lrintf(span / cfg->grid_step_deg);
    // Every angle needs its mirror on the grid, so the step must divide the range.
    if (steps < 1 || (float)steps * cfg->grid_step_deg != span) { return false; }
    const float bin_hz = (float)GEN_GRID_SAMPLE_RATE_HZ / (float)GEN_GRID_FFT_SIZE;
    const float first = ceilf(cfg->band_min_hz / bin_hz);
    const float last = floorf(cfg->band_max_hz / bin_hz);
    g->first = first < 1.0f ? 1 : (size_t)first;
    const size_t top = last > GEN_GRID_N_BINS - 1 ? GEN_GRID_N_BINS - 1 : (size_t)last;
    if (g->first > top) { return false; }
    g->n_bins = top - g->first + 1;
    g->n_angles = (size_t)steps + 1;
    g->n_half = (g->n_angles + 1) / 2;
    return true;
}

// One carving for sizing and building, so the two cannot disagree.
static dsp_afe_doa_t *carve(afe_arena_t *a, const geometry_t *g)
{
    dsp_afe_doa_t *st = afe_take(a, sizeof(*st));
    dsp_spec_cplx_t *cross = afe_take(a, g->n_bins * sizeof(dsp_spec_cplx_t));
    dsp_spec_cplx_t *start = afe_take(a, g->n_half * sizeof(dsp_spec_cplx_t));
    dsp_spec_cplx_t *turn = afe_take(a, g->n_half * sizeof(dsp_spec_cplx_t));
    float *unit_re = afe_take(a, g->n_bins * sizeof(float));
    float *unit_im = afe_take(a, g->n_bins * sizeof(float));
    float *response = afe_take(a, g->n_angles * sizeof(float));
    if (response == NULL) { return NULL; }
    *st = (struct dsp_afe_doa_s){.g = *g,
                                 .cross = cross,
                                 .start = start,
                                 .turn = turn,
                                 .unit_re = unit_re,
                                 .unit_im = unit_im,
                                 .response = response};
    return st;
}

size_t dsp_afe_doa_workspace_bytes(const dsp_afe_doa_config_t *cfg)
{
    geometry_t g;
    if (!geometry_of(cfg, &g)) { return 0; }
    afe_arena_t sizing = afe_arena(NULL, 0);
    carve(&sizing, &g);
    return AFE_ALIGN_BYTES + sizing.used;
}

static void fill_phasors(dsp_afe_doa_t *st, const dsp_afe_doa_config_t *cfg)
{
    const double bin_hz = (double)GEN_GRID_SAMPLE_RATE_HZ / GEN_GRID_FFT_SIZE;
    for (size_t i = 0; i < st->g.n_half; i++) {
        const double theta_rad = ((double)GEN_ARRAY_DOA_MIN_DEG + (double)i * (double)cfg->grid_step_deg) *
                                 AFE_PI / DEG_PER_HALF_TURN;
        const double tau_s =
            (double)cfg->spacing_m * afe_cos_series(theta_rad) / (double)cfg->speed_of_sound_m_s;
        const double per_bin = 2.0 * AFE_PI * bin_hz * tau_s;
        const double at_first = per_bin * (double)st->g.first;
        st->turn[i] = (dsp_spec_cplx_t){(float)afe_cos_series(per_bin), (float)afe_sin_series(per_bin)};
        st->start[i] = (dsp_spec_cplx_t){(float)afe_cos_series(at_first), (float)afe_sin_series(at_first)};
    }
}

esp_err_t dsp_afe_doa_init(dsp_afe_doa_t **out, const dsp_afe_doa_config_t *cfg, void *mem, size_t bytes)
{
    geometry_t g;
    if (out == NULL || mem == NULL || !geometry_of(cfg, &g)) { return ESP_ERR_INVALID_ARG; }
    afe_arena_t arena = afe_arena(mem, bytes);
    dsp_afe_doa_t *st = carve(&arena, &g);
    if (st == NULL) { return ESP_ERR_INVALID_SIZE; }
    fill_phasors(st, cfg);
    const double hop_s = (double)GEN_GRID_HOP_SAMPLES / GEN_GRID_SAMPLE_RATE_HZ;
    st->step_deg = cfg->grid_step_deg;
    st->keep = (float)afe_exp_series(-hop_s / (double)cfg->smooth_tau_s);
    st->take = 1.0f - st->keep;
    dsp_afe_doa_reset(st);
    *out = st;
    return ESP_OK;
}

void dsp_afe_doa_reset(dsp_afe_doa_t *st)
{
    memset(st->cross, 0, st->g.n_bins * sizeof(*st->cross));
    st->last = (dsp_afe_doa_result_t){.angle_deg = ANGLE_UNKNOWN_DEG, .confidence = 0};
}

static void fold(dsp_afe_doa_t *st, const dsp_spec_cplx_t *x0, const dsp_spec_cplx_t *x1)
{
    for (size_t k = 0; k < st->g.n_bins; k++) {
        const float cross_re = x0[k].re * x1[k].re + x0[k].im * x1[k].im;
        const float cross_im = x0[k].im * x1[k].re - x0[k].re * x1[k].im;
        st->cross[k].re = st->keep * st->cross[k].re + st->take * cross_re;
        st->cross[k].im = st->keep * st->cross[k].im + st->take * cross_im;
    }
}

static size_t normalise(dsp_afe_doa_t *st)
{
    size_t live = 0;
    for (size_t k = 0; k < st->g.n_bins; k++) {
        const float power = st->cross[k].re * st->cross[k].re + st->cross[k].im * st->cross[k].im;
        // The Newton 1 / sqrt needs a normal float; a bin below FLT_MIN carries no phase anyway.
        const float inv = power >= FLT_MIN ? afe_rsqrt_f32(power) : 0.0f;
        st->unit_re[k] = st->cross[k].re * inv;
        st->unit_im[k] = st->cross[k].im * inv;
        live += power >= FLT_MIN ? 1u : 0u;
    }
    return live;
}

static void steer(dsp_afe_doa_t *st)
{
    const size_t n = st->g.n_bins;
    for (size_t i = 0; i < st->g.n_half; i++) {
        float c = st->start[i].re;
        float s = st->start[i].im;
        const float turn_c = st->turn[i].re;
        const float turn_s = st->turn[i].im;
        float sum_c = 0.0f;
        float sum_s = 0.0f;
        for (size_t k = 0; k < n; k++) {
            sum_c = sum_c + st->unit_re[k] * c;
            sum_s = sum_s + st->unit_im[k] * s;
            const float next_c = c * turn_c - s * turn_s;
            s = c * turn_s + s * turn_c;
            c = next_c;
        }
        st->response[i] = sum_c - sum_s;
        const size_t mirror = st->g.n_angles - 1 - i;
        if (mirror != i) { st->response[mirror] = sum_c + sum_s; }
    }
}

static void search(dsp_afe_doa_t *st)
{
    const size_t live = normalise(st);
    if (live == 0) { return; }
    steer(st);
    size_t best = 0;
    float total = 0.0f;
    for (size_t i = 0; i < st->g.n_angles; i++) {
        best = st->response[i] > st->response[best] ? i : best;
        total = total + st->response[i];
    }
    const float peak = st->response[best];
    const float mean = total / (float)st->g.n_angles;
    const float lifted = peak + (float)live;
    long confidence = lifted > 0.0f ? lrintf(CONFIDENCE_MAX * (peak - mean) / lifted) : 0;
    confidence = confidence < 0 ? 0 : confidence > (long)CONFIDENCE_MAX ? (long)CONFIDENCE_MAX : confidence;
    st->last.angle_deg = (int16_t)lrintf((float)GEN_ARRAY_DOA_MIN_DEG + (float)best * st->step_deg);
    st->last.confidence = (uint8_t)confidence;
}

esp_err_t dsp_afe_doa_process(dsp_afe_doa_t *st, const dsp_spec_cplx_t *x0, const dsp_spec_cplx_t *x1,
                              bool update, dsp_afe_doa_result_t *out)
{
    if (st == NULL || x0 == NULL || x1 == NULL || out == NULL) { return ESP_ERR_INVALID_ARG; }
    fold(st, x0 + st->g.first, x1 + st->g.first);
    if (update) { search(st); }
    *out = st->last;
    return ESP_OK;
}
