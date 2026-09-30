#include <math.h>
#include <string.h>

#include "dsp_spec/pitch.h"
#include "gen_grid.h"
#include "spec_internal.h"

typedef struct {
    int decimation;
    int shift;
    int taps_before;
    int down_taps;
    int down_delay;
    int window;
    int first_lag;
    int measured;
    int frame_length;
    int n_lags;
    int up_taps;
    int context;
    int ring; // frames of traceback history kept
    int in_cap;
    int down_cap;
} layout_t;

typedef struct {
    int16_t state; // traced state these values belong to, -1 for none
    float pov;
    float log_pitch;
} traced_t;

struct dsp_spec_pitch_s {
    dsp_spec_pitch_config_t cfg;
    layout_t d;
    float factor;
    float *down_weights;
    float *lags;
    float *up_weights;
    int16_t *up_first;
    float *delta_scales;
    float *in;
    int in_len;
    int64_t in_offset;
    float *down;
    int down_len;
    int64_t down_offset;
    int64_t down_count;
    double sum;
    double sumsq;
    uint32_t frames;
    float *forward;
    int16_t *backpointers;
    float *pov_nccf;
    traced_t *traced;
    float latest_nccf;
    float latest_f0_hz;
    float *z;
    float *nccf_pitch;
    float *nccf_pov;
    float *pitch_lags;
    float *pov_lags;
    float *anchor;
    float *envelope_z;
    float *values;
    int16_t *envelope_v;
    int16_t *best;
    int16_t *path;
};

static bool config_ok(const dsp_spec_pitch_config_t *cfg)
{
    if (cfg == NULL || !(cfg->resample_hz > 0.0f) || !(cfg->lowpass_cutoff_hz > 0.0f) ||
        cfg->lowpass_zeros == 0 || cfg->upsample_zeros == 0 || !(cfg->window_s > 0.0f) ||
        !(cfg->min_f0_hz > 0.0f) || !(cfg->max_f0_hz > cfg->min_f0_hz) || !(cfg->delta_pitch > 0.0f) ||
        cfg->delta_window == 0 || !(cfg->normalization_left_s >= 0.0f)) {
        return false;
    }
    const double rate = cfg->resample_hz;
    return fmod(GEN_GRID_SAMPLE_RATE_HZ, rate) == 0.0 &&
           fmod(GEN_GRID_HOP_SAMPLES * rate, GEN_GRID_SAMPLE_RATE_HZ) == 0.0;
}

static int count_lags(const dsp_spec_pitch_config_t *cfg)
{
    float lag = (float)(1.0 / (double)cfg->max_f0_hz);
    const float last = (float)(1.0 / (double)cfg->min_f0_hz);
    const double step = 1.0 + (double)cfg->delta_pitch;
    int n = 0;
    for (; lag <= last; n++) {
        lag = (float)((double)lag * step);
    }
    return n;
}

static void fill_lags(const dsp_spec_pitch_config_t *cfg, float *lags, int n)
{
    float lag = (float)(1.0 / (double)cfg->max_f0_hz);
    const double step = 1.0 + (double)cfg->delta_pitch;
    for (int i = 0; i < n; i++) {
        lags[i] = lag;
        lag = (float)((double)lag * step);
    }
}

static void lag_bounds(const dsp_spec_pitch_config_t *cfg, const layout_t *d, float lag, int *low, int *high)
{
    const float point = lag - (float)((double)d->first_lag / (double)cfg->resample_hz);
    const float width = (float)(cfg->upsample_zeros / (2.0 * ((double)cfg->resample_hz * 0.5)));
    const float rate = cfg->resample_hz;
    const int lo = (int)ceilf(rate * (point - width));
    const int hi = (int)floorf(rate * (point + width));
    *low = lo < 0 ? 0 : lo;
    *high = hi > d->measured - 1 ? d->measured - 1 : hi;
}

static bool plan_layout(const dsp_spec_pitch_config_t *cfg, layout_t *d)
{
    if (!config_ok(cfg)) { return false; }
    const double rate = GEN_GRID_SAMPLE_RATE_HZ, resample = cfg->resample_hz;
    d->decimation = (int)(rate / resample);
    d->shift = (int)(GEN_GRID_HOP_SAMPLES * resample / rate);
    const double half = cfg->lowpass_zeros / (2.0 * (double)cfg->lowpass_cutoff_hz);
    d->taps_before = (int)ceil(-half * rate);
    d->down_delay = (int)floor(half * rate);
    d->down_taps = d->down_delay - d->taps_before + 1;
    d->window = (int)(resample * (double)cfg->window_s);
    const double widen = cfg->upsample_zeros / (2.0 * resample);
    d->first_lag = (int)ceil(resample * (1.0 / (double)cfg->max_f0_hz - widen));
    const int last_lag = (int)floor(resample * (1.0 / (double)cfg->min_f0_hz + widen));
    d->measured = last_lag + 1 - d->first_lag;
    d->frame_length = d->window + last_lag;
    d->n_lags = count_lags(cfg);
    if (d->window <= 0 || d->first_lag <= 0 || d->measured <= 0 || d->n_lags <= 0 || d->n_lags > INT16_MAX) {
        return false;
    }
    float lag = (float)(1.0 / (double)cfg->max_f0_hz);
    const double step = 1.0 + (double)cfg->delta_pitch;
    d->up_taps = 0;
    for (int i = 0; i < d->n_lags; i++) {
        int lo, hi;
        lag_bounds(cfg, d, lag, &lo, &hi);
        d->up_taps = hi - lo + 1 > d->up_taps ? hi - lo + 1 : d->up_taps;
        lag = (float)((double)lag * step);
    }
    d->context = (int)nearbyint((double)cfg->normalization_left_s * rate / GEN_GRID_HOP_SAMPLES);
    d->ring = (d->context > cfg->delta_window ? d->context : cfg->delta_window) + 1;
    d->in_cap = GEN_GRID_HOP_SAMPLES + 2 * d->down_taps;
    d->down_cap = d->frame_length + 2 * d->shift;
    return d->up_taps > 0;
}

static size_t floats(int n)
{
    return spec_round_up((size_t)n * sizeof(float));
}

static size_t shorts(int n)
{
    return spec_round_up((size_t)n * sizeof(int16_t));
}

static size_t arena_bytes(const layout_t *d, int delta_terms)
{
    return spec_round_up(sizeof(struct dsp_spec_pitch_s)) + floats(d->down_taps) + floats(d->n_lags) +
           floats(d->n_lags * d->up_taps) + shorts(d->n_lags) + floats(delta_terms) + floats(d->in_cap) +
           floats(d->down_cap) + floats(d->n_lags) + shorts(d->ring * d->n_lags) +
           floats(d->ring * d->n_lags) + spec_round_up((size_t)d->ring * sizeof(traced_t)) +
           floats(d->frame_length) + 2 * floats(d->measured) + 4 * floats(d->n_lags) + floats(d->n_lags + 1) +
           2 * shorts(d->n_lags) + shorts(d->ring);
}

size_t dsp_spec_pitch_workspace_bytes(const dsp_spec_pitch_config_t *cfg)
{
    layout_t d;
    return plan_layout(cfg, &d) ? SPEC_ALIGN_BYTES + arena_bytes(&d, 2 * cfg->delta_window + 1) : 0;
}

static double sinc_value(double t, double cutoff_hz, int zeros)
{
    if (!(fabs(t) < zeros / (2.0 * cutoff_hz))) { return 0.0; }
    const double window = 0.5 * (1.0 + cos(2.0 * M_PI * cutoff_hz / zeros * t));
    return (t == 0.0 ? 2.0 * cutoff_hz : sin(2.0 * M_PI * cutoff_hz * t) / (M_PI * t)) * window;
}

static void build_tables(dsp_spec_pitch_t *p)
{
    const dsp_spec_pitch_config_t *cfg = &p->cfg;
    const layout_t *d = &p->d;
    const double rate = GEN_GRID_SAMPLE_RATE_HZ;
    for (int k = 0; k < d->down_taps; k++) {
        const int tap = d->taps_before + k;
        p->down_weights[k] =
            (float)(sinc_value((double)tap / rate, cfg->lowpass_cutoff_hz, cfg->lowpass_zeros) / rate);
    }
    fill_lags(cfg, p->lags, d->n_lags);
    const double cutoff = (double)cfg->resample_hz * 0.5;
    const float first = (float)((double)d->first_lag / (double)cfg->resample_hz);
    memset(p->up_weights, 0, (size_t)d->n_lags * d->up_taps * sizeof(float));
    for (int i = 0; i < d->n_lags; i++) {
        int lo, hi;
        lag_bounds(cfg, d, p->lags[i], &lo, &hi);
        p->up_first[i] = (int16_t)lo;
        const float point = p->lags[i] - first;
        for (int j = 0; j <= hi - lo; j++) {
            const float t = (float)((double)point - (double)(lo + j) / (double)cfg->resample_hz);
            p->up_weights[i * d->up_taps + j] =
                (float)(sinc_value(t, cutoff, cfg->upsample_zeros) / (double)cfg->resample_hz);
        }
    }
    const int dw = cfg->delta_window;
    double norm = 0.0;
    for (int j = -dw; j <= dw; j++) {
        norm += (double)(j * j);
    }
    for (int j = -dw; j <= dw; j++) {
        p->delta_scales[j + dw] = (float)((double)j / norm);
    }
    const double step_log = log(1.0 + (double)cfg->delta_pitch);
    p->factor = (float)(step_log * step_log) * cfg->penalty_factor;
}

esp_err_t dsp_spec_pitch_init(dsp_spec_pitch_t **out, const dsp_spec_pitch_config_t *cfg, void *mem,
                              size_t bytes)
{
    const size_t need = dsp_spec_pitch_workspace_bytes(cfg);
    if (out == NULL || mem == NULL || need == 0) { return ESP_ERR_INVALID_ARG; }
    if (bytes < need) { return ESP_ERR_INVALID_SIZE; }
    spec_carver_t c = spec_carver(mem, bytes);
    dsp_spec_pitch_t *p = spec_carve(&c, sizeof(*p));
    memset(p, 0, sizeof(*p));
    p->cfg = *cfg;
    plan_layout(cfg, &p->d);
    const layout_t *d = &p->d;
    p->down_weights = spec_carve(&c, (size_t)d->down_taps * sizeof(float));
    p->lags = spec_carve(&c, (size_t)d->n_lags * sizeof(float));
    p->up_weights = spec_carve(&c, (size_t)d->n_lags * d->up_taps * sizeof(float));
    p->up_first = spec_carve(&c, (size_t)d->n_lags * sizeof(int16_t));
    p->delta_scales = spec_carve(&c, (size_t)(2 * cfg->delta_window + 1) * sizeof(float));
    p->in = spec_carve(&c, (size_t)d->in_cap * sizeof(float));
    p->down = spec_carve(&c, (size_t)d->down_cap * sizeof(float));
    p->forward = spec_carve(&c, (size_t)d->n_lags * sizeof(float));
    p->backpointers = spec_carve(&c, (size_t)d->ring * d->n_lags * sizeof(int16_t));
    p->pov_nccf = spec_carve(&c, (size_t)d->ring * d->n_lags * sizeof(float));
    p->traced = spec_carve(&c, (size_t)d->ring * sizeof(traced_t));
    p->z = spec_carve(&c, (size_t)d->frame_length * sizeof(float));
    p->nccf_pitch = spec_carve(&c, (size_t)d->measured * sizeof(float));
    p->nccf_pov = spec_carve(&c, (size_t)d->measured * sizeof(float));
    p->pitch_lags = spec_carve(&c, (size_t)d->n_lags * sizeof(float));
    p->pov_lags = spec_carve(&c, (size_t)d->n_lags * sizeof(float));
    p->anchor = spec_carve(&c, (size_t)d->n_lags * sizeof(float));
    p->values = spec_carve(&c, (size_t)d->n_lags * sizeof(float));
    p->envelope_z = spec_carve(&c, (size_t)(d->n_lags + 1) * sizeof(float));
    p->envelope_v = spec_carve(&c, (size_t)d->n_lags * sizeof(int16_t));
    p->best = spec_carve(&c, (size_t)d->n_lags * sizeof(int16_t));
    p->path = spec_carve(&c, (size_t)d->ring * sizeof(int16_t));
    if (p->path == NULL) { return ESP_ERR_INVALID_SIZE; }
    build_tables(p);
    dsp_spec_pitch_reset(p);
    *out = p;
    return ESP_OK;
}

void dsp_spec_pitch_reset(dsp_spec_pitch_t *pitch)
{
    if (pitch == NULL) { return; }
    pitch->in_len = 0;
    pitch->in_offset = 0;
    pitch->down_len = 0;
    pitch->down_offset = 0;
    pitch->down_count = 0;
    pitch->sum = 0.0;
    pitch->sumsq = 0.0;
    pitch->frames = 0;
    memset(pitch->forward, 0, (size_t)pitch->d.n_lags * sizeof(float));
    for (int s = 0; s < pitch->d.ring; s++) {
        pitch->traced[s].state = -1;
    }
    pitch->latest_nccf = 0.0f;
    pitch->latest_f0_hz = 0.0f;
}

// Sums run term by term from the first product, as numpy's cumsum does, so both sides round alike.
static float seq_dot(const float *a, const float *b, int n)
{
    float acc = a[0] * b[0];
    for (int i = 1; i < n; i++) {
        acc += a[i] * b[i];
    }
    return acc;
}

static int downsample(dsp_spec_pitch_t *p)
{
    const layout_t *d = &p->d;
    const int64_t have = p->in_offset + p->in_len;
    const int64_t last = (have - 1 - d->down_delay) / d->decimation;
    const int first_new = p->down_len;
    for (int64_t n = p->down_count; n <= last; n++) {
        const int64_t start = n * d->decimation + d->taps_before;
        float acc = 0.0f;
        for (int j = 0; j < d->down_taps; j++) {
            const int64_t at = start + j;
            const float x = at >= 0 ? p->in[at - p->in_offset] : 0.0f;
            acc = j == 0 ? p->down_weights[0] * x : acc + p->down_weights[j] * x;
        }
        p->down[p->down_len++] = acc;
    }
    const int made = p->down_len - first_new;
    p->down_count += made;
    int64_t keep = p->down_count * d->decimation + d->taps_before;
    keep = keep < 0 ? 0 : keep;
    const int drop = (int)(keep - p->in_offset);
    memmove(p->in, p->in + drop, (size_t)(p->in_len - drop) * sizeof(float));
    p->in_len -= drop;
    p->in_offset = keep;
    return made;
}

static void correlate(dsp_spec_pitch_t *p, const float *frame, float ballast)
{
    const layout_t *d = &p->d;
    const int w = d->window;
    float sum = frame[0];
    for (int i = 1; i < w; i++) {
        sum += frame[i];
    }
    const float mean = sum / (float)w;
    for (int i = 0; i < d->frame_length; i++) {
        p->z[i] = frame[i] - mean;
    }
    const float e1 = seq_dot(p->z, p->z, w);
    for (int l = 0; l < d->measured; l++) {
        const float *shifted = p->z + d->first_lag + l;
        const float e2 = seq_dot(shifted, shifted, w);
        const float inner = seq_dot(shifted, p->z, w);
        const float norm = e1 * e2;
        const float with_ballast = sqrtf(norm + ballast);
        const float without = sqrtf(norm);
        p->nccf_pitch[l] = with_ballast != 0.0f ? inner / with_ballast : 0.0f;
        p->nccf_pov[l] = without != 0.0f ? inner / without : 0.0f;
    }
}

static void resample_lags(const dsp_spec_pitch_t *p, const float *measured, float *out)
{
    const layout_t *d = &p->d;
    for (int i = 0; i < d->n_lags; i++) {
        const float *w = p->up_weights + i * d->up_taps;
        float acc = 0.0f;
        for (int j = 0; j < d->up_taps; j++) {
            int at = p->up_first[i] + j;
            at = at > d->measured - 1 ? d->measured - 1 : at;
            acc = j == 0 ? w[0] * measured[at] : acc + w[j] * measured[at];
        }
        out[i] = acc;
    }
}

// Lower envelope of the parabolas factor (i - j)^2 + prev[j] (Felzenszwalb and Huttenlocher, 2012).
static void distance_transform(dsp_spec_pitch_t *p)
{
    const int n = p->d.n_lags;
    const float factor = p->factor, two_factor = 2.0f * factor;
    for (int i = 0; i < n; i++) {
        const float x = (float)i;
        p->anchor[i] = p->forward[i] + factor * x * x;
    }
    int16_t *v = p->envelope_v;
    float *z = p->envelope_z;
    int k = 0;
    v[0] = 0;
    z[0] = -INFINITY;
    z[1] = INFINITY;
    for (int q = 1; q < n; q++) {
        float s = (p->anchor[q] - p->anchor[v[k]]) / (two_factor * (float)(q - v[k]));
        while (s <= z[k]) {
            k--;
            s = (p->anchor[q] - p->anchor[v[k]]) / (two_factor * (float)(q - v[k]));
        }
        k++;
        v[k] = (int16_t)q;
        z[k] = s;
        z[k + 1] = INFINITY;
    }
    k = 0;
    for (int i = 0; i < n; i++) {
        while (z[k + 1] < (float)i) {
            k++;
        }
        p->best[i] = v[k];
        const float d = (float)(i - v[k]);
        p->values[i] = (d * d) * factor + p->forward[v[k]];
    }
}

static void track_frame(dsp_spec_pitch_t *p, uint32_t f)
{
    const layout_t *d = &p->d;
    const double n = (double)p->down_count;
    const double mean_square = p->sumsq / n - (p->sum / n) * (p->sum / n);
    const double scaled = mean_square * d->window;
    const float ballast = (float)(scaled * scaled * (double)p->cfg.nccf_ballast);
    correlate(p, p->down + ((int64_t)f * d->shift - p->down_offset), ballast);
    resample_lags(p, p->nccf_pitch, p->pitch_lags);
    resample_lags(p, p->nccf_pov, p->pov_lags);
    distance_transform(p);
    float least = INFINITY;
    for (int i = 0; i < d->n_lags; i++) {
        const float local = (1.0f - p->pitch_lags[i]) + (p->cfg.soft_min_f0 * p->lags[i]) * p->pitch_lags[i];
        p->forward[i] = p->values[i] + local;
        least = p->forward[i] < least ? p->forward[i] : least;
    }
    for (int i = 0; i < d->n_lags; i++) {
        p->forward[i] = p->forward[i] - least;
    }
    const int slot = (int)(f % (uint32_t)d->ring);
    memcpy(p->backpointers + slot * d->n_lags, p->best, (size_t)d->n_lags * sizeof(int16_t));
    memcpy(p->pov_nccf + slot * d->n_lags, p->pov_lags, (size_t)d->n_lags * sizeof(float));
    p->traced[slot].state = -1;
}

static float nccf_to_pov(float nccf)
{
    const double a = fmin(fabs((double)nccf), 1.0);
    const double r =
        -5.2 + 5.4 * exp(7.5 * (a - 1.0)) + 4.8 * a - 2.0 * exp(-10.0 * a) + 4.2 * exp(20.0 * (a - 1.0));
    return (float)(1.0 / (1.0 + exp(-r)));
}

static float nccf_to_pov_feature(float nccf)
{
    const double c = fmin(fmax((double)nccf, -1.0), 1.0);
    return (float)(pow(1.0001 - c, 0.15) - 1.0);
}

static const traced_t *traced_at(dsp_spec_pitch_t *p, int slot, int16_t state)
{
    traced_t *t = &p->traced[slot];
    if (t->state != state) {
        const float pitch_hz = (float)(1.0 / (double)p->lags[state]);
        t->state = state;
        t->pov = nccf_to_pov(p->pov_nccf[slot * p->d.n_lags + state]);
        t->log_pitch = (float)log((double)pitch_hz);
    }
    return t;
}

static void features_of_newest(dsp_spec_pitch_t *p, float *out)
{
    const layout_t *d = &p->d;
    const int n = d->n_lags;
    int16_t state = 0;
    for (int i = 1; i < n; i++) {
        state = p->forward[i] < p->forward[state] ? (int16_t)i : state;
    }
    const int count = p->frames < (uint32_t)d->ring ? (int)p->frames : d->ring;
    const uint32_t newest_frame = p->frames - 1;
    p->path[count - 1] = state;
    for (int k = count - 1; k > 0; k--) {
        const int slot = (int)((newest_frame - (uint32_t)(count - 1 - k)) % (uint32_t)d->ring);
        p->path[k - 1] = p->backpointers[slot * n + p->path[k]];
    }
    const int window = count < d->context + 1 ? count : d->context + 1;
    float sum_pov = 0.0f, sum_log_pitch_pov = 0.0f;
    for (int k = count - window; k < count; k++) {
        const int slot = (int)((newest_frame - (uint32_t)(count - 1 - k)) % (uint32_t)d->ring);
        const traced_t *t = traced_at(p, slot, p->path[k]);
        sum_pov += t->pov;
        sum_log_pitch_pov += t->pov * t->log_pitch;
    }
    const int newest_slot = (int)(newest_frame % (uint32_t)d->ring);
    const float newest_log_pitch = traced_at(p, newest_slot, state)->log_pitch;
    const int dw = p->cfg.delta_window;
    const int lowest = (count - 1) - ((count - 1) < dw ? (count - 1) : dw);
    float delta = 0.0f;
    for (int j = -dw; j <= dw; j++) {
        const float scale = p->delta_scales[j + dw];
        if (scale == 0.0f) { continue; }
        int k = count - 1 + j;
        k = k < lowest ? lowest : (k > count - 1 ? count - 1 : k);
        const int slot = (int)((newest_frame - (uint32_t)(count - 1 - k)) % (uint32_t)d->ring);
        delta += scale * traced_at(p, slot, p->path[k])->log_pitch;
    }
    const float nccf = p->pov_nccf[newest_slot * n + state];
    out[0] = p->cfg.pov_scale * nccf_to_pov_feature(nccf);
    out[1] = (newest_log_pitch - sum_log_pitch_pov / sum_pov) * p->cfg.pitch_scale;
    out[2] = delta * p->cfg.delta_pitch_scale;
    p->latest_nccf = nccf;
    p->latest_f0_hz = (float)(1.0 / (double)p->lags[state]);
}

esp_err_t dsp_spec_pitch_frame(dsp_spec_pitch_t *pitch, const float *hop, float *features)
{
    if (pitch == NULL || hop == NULL || features == NULL) { return ESP_ERR_INVALID_ARG; }
    dsp_spec_pitch_t *p = pitch;
    const layout_t *d = &p->d;
    memcpy(p->in + p->in_len, hop, GEN_GRID_HOP_SAMPLES * sizeof(float));
    p->in_len += GEN_GRID_HOP_SAMPLES;
    const int first_new = p->down_len;
    const int made = downsample(p);
    if (made > 0) {
        const float *fresh = p->down + first_new;
        float sum = fresh[0];
        for (int i = 1; i < made; i++) {
            sum += fresh[i];
        }
        // Each hop summed in float, the running totals in double, as Kaldi adds each chunk's VecVec.
        p->sum += (double)sum;
        p->sumsq += (double)seq_dot(fresh, fresh, made);
    }
    const uint32_t ready =
        p->down_count >= d->frame_length ? (uint32_t)((p->down_count - d->frame_length) / d->shift + 1) : 0;
    if (ready == p->frames) {
        memset(features, 0, DSP_SPEC_PITCH_FEATURES * sizeof(float));
        return ESP_OK;
    }
    for (uint32_t f = p->frames; f < ready; f++) {
        track_frame(p, f);
    }
    p->frames = ready;
    const int64_t keep = (int64_t)p->frames * d->shift;
    const int drop = (int)(keep - p->down_offset);
    memmove(p->down, p->down + drop, (size_t)(p->down_len - drop) * sizeof(float));
    p->down_len -= drop;
    p->down_offset = keep;
    features_of_newest(p, features);
    return ESP_OK;
}

void dsp_spec_pitch_latest(const dsp_spec_pitch_t *pitch, float *nccf, float *f0_hz)
{
    if (pitch == NULL || nccf == NULL || f0_hz == NULL) { return; }
    *nccf = pitch->latest_nccf;
    *f0_hz = pitch->latest_f0_hz;
}
