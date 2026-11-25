#include "ns_ulunas/ulunas.h"

#include <math.h>
#include <stdbool.h>
#include <string.h>

#define BINS AI_ENGINE_ULUNAS_BINS
#define LOW AI_ENGINE_ULUNAS_LOW
#define ERB AI_ENGINE_ULUNAS_ERB
#define UNITS AI_ENGINE_ULUNAS_UNITS
#define ERB_BANDS (ERB - LOW)
#define HIGH_BINS (BINS - LOW)
#define CHUNK 4                // frequency bins a step of the attention's frequency GRU
#define BLOB_MAGIC 0x31554c55u // "ULU1"
#define BLOB_VERSION 1u

// The mirror's float32 constants, bit for bit (ulunas_mirror.py).
static const float kMagFloor = 0x1.197998p-40f;
static const float kSqrtHalf = 0x1.6a09e6p-1f;
static const float kLog10_2Hi = 0x1.344136p-2f;
static const float kLog10_2Lo = -0x1.ec10cp-27f;
static const float kLog10E2 = 0x1.bcb7b2p-1f;
static const float kAtanhPoly[] = {0x1.c71c72p-4f, 0x1.24924ap-3f, 0x1.99999ap-3f, 0x1.555556p-2f, 1.0f};

typedef enum { XCONV, XDWS, XMB } kind_t;

typedef struct {
    uint8_t kind;
    uint8_t cin, cout;
    uint8_t kt, kf, stride, groups;
    uint8_t fin, fout;
    bool deconv, last;
    uint16_t cache_at; // its kt - 1 past frames in the state's cache
    uint16_t tfa_at;   // into the state's tfa; 2 cout hidden units
} unit_t;

// The upstream default net: encoder rows, then the decoder's rows mirrored (ulunas_mirror.py's config).
static const unit_t kEnc[UNITS] = {
    {XCONV, 1, 12, 3, 3, 2, 1, ERB, 65, false, false, 0, 0},
    {XMB, 12, 24, 2, 3, 2, 2, 65, 33, false, false, 258, 24},
    {XDWS, 24, 24, 2, 3, 1, 2, 33, 33, false, false, 1818, 72},
    {XMB, 24, 32, 1, 5, 1, 2, 33, 33, false, false, 0, 120},
    {XDWS, 32, 16, 1, 5, 1, 2, 33, 33, false, false, 0, 184},
};
static const unit_t kDec[UNITS] = {
    {XDWS, 16, 32, 1, 5, 1, 2, 33, 33, true, false, 0, 216},
    {XMB, 32, 24, 1, 5, 1, 2, 33, 33, true, false, 0, 280},
    {XDWS, 24, 24, 2, 3, 1, 2, 33, 33, true, false, 2610, 328},
    {XMB, 24, 12, 2, 3, 2, 2, 33, 65, true, false, 3402, 376},
    {XCONV, 12, 1, 3, 3, 2, 1, 65, ERB, true, true, 3798, 400},
};
static const uint16_t kSkipAt[UNITS] = {0, 780, 1572, 2364, 3420};

static inline __attribute__((always_inline)) float log10_32(float v)
{
    int e = 0;
    float m = split(v, &e);
    if (m < kSqrtHalf) {
        m = m * 2.0f;
        e -= 1;
    }
    const float s = (m - 1.0f) * recip32(m + 1.0f);
    const float s2 = s * s;
    float p = kAtanhPoly[0];
    for (size_t k = 1; k < sizeof(kAtanhPoly) / sizeof(kAtanhPoly[0]); k++) {
        p = fmaf(p, s2, kAtanhPoly[k]);
    }
    const float ef = (float)e;
    return fmaf(ef, kLog10_2Hi, fmaf(ef, kLog10_2Lo, (s * p) * kLog10E2));
}

static void affine_prelu(float *x, int ch, int width, const ai_engine_ulunas_layer_t *l)
{
    if (l->act_a == NULL) { return; }
    for (int c = 0; c < ch; c++) {
        const float slope = l->act_slope[c];
        for (int f = 0; f < width; f++) {
            const int i = c * width + f;
            const float v = x[i];
            x[i] = fmaf(l->act_a[i], v, l->act_b[i]) + (v > 0.0f ? v : slope * v);
        }
    }
}

// A grouped 1x1 convolution: each output summed over its group's inputs in order.
static void point(const float *x, int cin, int nf, const ai_engine_ulunas_layer_t *l, int cout, int groups,
                  float *out)
{
    const int per_in = cin / groups;
    const int per_out = cout / groups;
    for (int co = 0; co < cout; co++) {
        const float *w = l->w + co * per_in;
        const float *xg = x + (co / per_out) * per_in * nf;
        for (int f = 0; f < nf; f++) {
            float acc = l->b[co];
            for (int ci = 0; ci < per_in; ci++) {
                acc = fmaf(w[ci], xg[ci * nf + f], acc);
            }
            out[co * nf + f] = acc;
        }
    }
}

// The causal convolution over span, (cin, kt, fin) oldest frame first: b, then taps over the group's ci, t,
// k, the zero padding included.
static void conv_span(const float *span, int cin, const unit_t *u, const ai_engine_ulunas_layer_t *l,
                      int cout, int groups, float *out)
{
    const int kt = u->kt;
    const int kf = u->kf;
    const int fin = u->fin;
    const int pad = kf / 2;
    const int per_in = cin / groups;
    const int per_out = cout / groups;
    for (int co = 0; co < cout; co++) {
        const float *w = l->w + co * per_in * kt * kf;
        const float *sg = span + (co / per_out) * per_in * kt * fin;
        for (int fo = 0; fo < u->fout; fo++) {
            float acc = l->b[co];
            for (int r = 0; r < per_in * kt; r++) {
                const float *row = sg + r * fin;
                for (int k = 0; k < kf; k++) {
                    const int f = fo * u->stride - pad + k;
                    acc = fmaf(w[r * kf + k], f >= 0 && f < fin ? row[f] : 0.0f, acc);
                }
            }
            out[co * u->fout + fo] = acc;
        }
    }
}

// The last frame of the causal transposed convolution over span: b, then x[ci][fi] w[ci][co'][t][k] for frame
// kt - 1 - t over the group's ci, t, k, each fi = (fo + pad - k) / stride whole and in range.
static void deconv_span(const float *span, int cin, const unit_t *u, const ai_engine_ulunas_layer_t *l,
                        int cout, int groups, float *out)
{
    const int kt = u->kt;
    const int kf = u->kf;
    const int fin = u->fin;
    const int pad = kf / 2;
    const int per_in = cin / groups;
    const int per_out = cout / groups;
    for (int co = 0; co < cout; co++) {
        const int g = co / per_out;
        const int local = co % per_out;
        for (int fo = 0; fo < u->fout; fo++) {
            float acc = l->b[co];
            for (int ci = g * per_in; ci < (g + 1) * per_in; ci++) {
                const float *wc = l->w + (ci * per_out + local) * kt * kf;
                for (int t = 0; t < kt; t++) {
                    const float *row = span + (ci * kt + (kt - 1 - t)) * fin;
                    for (int k = 0; k < kf; k++) {
                        const int num = fo + pad - k;
                        if (num >= 0 && num % u->stride == 0 && num / u->stride < fin) {
                            acc = fmaf(row[num / u->stride], wc[t * kf + k], acc);
                        }
                    }
                }
            }
            out[co * u->fout + fo] = acc;
        }
    }
}

// The cache's kt - 1 frames, then x, into the span; the layer over it; then the cache moved on a frame.
static void temporal(const unit_t *u, const ai_engine_ulunas_layer_t *l, const float *x, int ch, int cout,
                     int groups, ai_engine_ulunas_state_t *st, float *out)
{
    const int kt = u->kt;
    const int fin = u->fin;
    float *cache = st->cache + u->cache_at;
    const float *span = x;
    if (kt > 1) {
        for (int c = 0; c < ch; c++) {
            memcpy(st->span + c * kt * fin, cache + c * (kt - 1) * fin,
                   (size_t)(kt - 1) * fin * sizeof(float));
            memcpy(st->span + (c * kt + kt - 1) * fin, x + c * fin, (size_t)fin * sizeof(float));
        }
        span = st->span;
    }
    if (u->deconv) {
        deconv_span(span, ch, u, l, cout, groups, out);
    } else {
        conv_span(span, ch, u, l, cout, groups, out);
    }
    if (kt > 1) {
        for (int c = 0; c < ch; c++) {
            memcpy(cache + c * (kt - 1) * fin, st->span + (c * kt + 1) * fin,
                   (size_t)(kt - 1) * fin * sizeof(float));
        }
    }
    affine_prelu(out, cout, u->fout, l);
}

static void shuffle(const float *x, int ch, int width, float *out)
{
    const int half = ch / 2;
    for (int c = 0; c < half; c++) {
        memcpy(out + 2 * c * width, x + c * width, (size_t)width * sizeof(float));
        memcpy(out + (2 * c + 1) * width, x + (half + c) * width, (size_t)width * sizeof(float));
    }
}

// cTFA over x in place: the time GRU on each channel's mean square moves h a hop; the frequency GRU runs both
// ways over the zero-padded chunks of the channels' mean square.
static void run_tfa(const ai_engine_ulunas_tfa_t *t, float *x, int ch, int width, float *h,
                    ai_engine_ulunas_state_t *st)
{
    float zt[32];
    float fresh[64] = {0};
    float at[32];
    for (int c = 0; c < ch; c++) {
        zt[c] = mean_square(x + c * width, width);
    }
    gru_step(&t->gru, ch, 2 * ch, zt, h, fresh);
    memcpy(h, fresh, (size_t)2 * ch * sizeof(float));
    dense(t->fc_w, t->fc_b, 2 * ch, ch, h, at);
    const int chunks = (width + CHUNK - 1) / CHUNK;
    for (int f = 0; f < chunks * CHUNK; f++) {
        float a = 0.0f;
        if (f < width) {
            for (int c = 0; c < ch; c++) {
                a = fmaf(x[c * width + f], x[c * width + f], a);
            }
            a = a / (float)ch;
        }
        st->zf[f] = a;
    }
    for (int dir = 0; dir < 2; dir++) {
        float state[CHUNK] = {0};
        for (int i = 0; i < chunks; i++) {
            const int p = dir == 0 ? i : chunks - 1 - i;
            float next[CHUNK];
            gru_step(&t->fa[dir], CHUNK, CHUNK, st->zf + p * CHUNK, state, next);
            memcpy(state, next, sizeof(state));
            memcpy(st->both + p * 2 * CHUNK + dir * CHUNK, state, sizeof(state));
        }
    }
    for (int p = 0; p < chunks; p++) {
        dense(t->fa_fc_w, t->fa_fc_b, 2 * CHUNK, CHUNK, st->both + p * 2 * CHUNK, st->af + p * CHUNK);
    }
    for (int f = 0; f < width; f++) {
        st->af[f] = sigmoid32(st->af[f]);
    }
    for (int c = 0; c < ch; c++) {
        const float sc = sigmoid32(at[c]);
        for (int f = 0; f < width; f++) {
            x[c * width + f] = (sc * x[c * width + f]) * st->af[f];
        }
    }
}

static void run_unit(const unit_t *u, const ai_engine_ulunas_unit_t *wt, ai_engine_ulunas_state_t *st,
                     const float *x, float *out)
{
    float *h = st->tfa + u->tfa_at;
    if (u->kind == XCONV) {
        temporal(u, &wt->layer[0], x, u->cin, u->cout, u->groups, st, out);
        run_tfa(&wt->tfa, out, u->cout, u->fout, h, st);
        return;
    }
    point(x, u->cin, u->fin, &wt->layer[0], u->cout, u->groups, st->p);
    affine_prelu(st->p, u->cout, u->fin, &wt->layer[0]);
    shuffle(st->p, u->cout, u->fin, st->q);
    if (u->kind == XDWS) {
        temporal(u, &wt->layer[1], st->q, u->cout, u->cout, u->cout, st, out);
        run_tfa(&wt->tfa, out, u->cout, u->fout, h, st);
        return;
    }
    temporal(u, &wt->layer[1], st->q, u->cout, u->cout, u->cout, st, st->d);
    point(st->d, u->cout, u->fout, &wt->layer[2], u->cout, u->groups, st->p);
    run_tfa(&wt->tfa, st->p, u->cout, u->fout, h, st);
    shuffle(st->p, u->cout, u->fout, out);
}

void ai_engine_ulunas_reset(ai_engine_ulunas_state_t *st)
{
    memset(st->cache, 0, sizeof(st->cache));
    memset(st->tfa, 0, sizeof(st->tfa));
    memset(st->inter, 0, sizeof(st->inter));
}

void ai_engine_ulunas_step(const ai_engine_ulunas_weights_t *w, ai_engine_ulunas_state_t *st,
                           const float *bins, float *gain)
{
    for (int i = 0; i < BINS; i++) {
        const float re = bins[2 * i] * w->scale;
        const float im = bins[2 * i + 1] * w->scale;
        const float mag = sqrt32(fmaf(re, re, im * im));
        st->feat[i] = log10_32(mag > kMagFloor ? mag : kMagFloor);
    }
    banded(st->feat, 1, BINS, LOW, w->erb_ptr, w->erb_idx, w->erb_coef, ERB_BANDS, ERB, st->erb);
    const float *in = st->erb;
    for (int k = 0; k < UNITS; k++) {
        float *skip = st->skip + kSkipAt[k];
        run_unit(&kEnc[k], &w->enc[k], st, in, skip);
        in = skip;
    }
    memcpy(st->y, in, NS_FLOAT_PLANE * sizeof(float));
    const ns_float_dual_work_t work = {.t = st->t,
                                       .y = st->dy,
                                       .u = st->u,
                                       .hn = st->hn,
                                       .gi = st->gi,
                                       .gh = st->gh,
                                       .rz = st->rz,
                                       .nn = st->nn};
    for (int k = 0; k < 2; k++) {
        run_dual(&w->dual[k], &work, st->inter[k], st->y);
    }
    for (int k = 0; k < UNITS; k++) {
        const unit_t *u = &kDec[k];
        const float *skip = st->skip + kSkipAt[UNITS - 1 - k];
        for (int i = 0; i < u->cin * u->fin; i++) {
            st->in[i] = st->y[i] + skip[i];
        }
        run_unit(u, &w->dec[k], st, st->in, st->y);
    }
    for (int f = 0; f < ERB; f++) {
        st->m[f] = sigmoid32(st->y[f]);
    }
    banded(st->m, 1, ERB, LOW, w->ierb_ptr, w->ierb_idx, w->ierb_coef, HIGH_BINS, BINS, st->mask);
    for (int i = 0; i < BINS; i++) {
        gain[i] = st->mask[i] < 1.0f ? st->mask[i] : 1.0f;
    }
}

static ai_engine_ulunas_layer_t take_layer(ns_float_cursor_t *c, size_t weights, int cout, int width,
                                           bool act)
{
    ai_engine_ulunas_layer_t l = {.w = ns_float_take(c, weights), .b = ns_float_take(c, (size_t)cout)};
    if (act) {
        l.act_a = ns_float_take(c, (size_t)cout * width);
        l.act_b = ns_float_take(c, (size_t)cout * width);
        l.act_slope = ns_float_take(c, (size_t)cout);
    }
    return l;
}

static ai_engine_ulunas_unit_t take_unit(ns_float_cursor_t *c, const unit_t *u)
{
    ai_engine_ulunas_unit_t v = {0};
    const size_t taps = (size_t)u->kt * u->kf;
    if (u->kind == XCONV) {
        v.layer[0] = take_layer(c, (size_t)u->cout * (u->cin / u->groups) * taps, u->cout, u->fout, !u->last);
    } else {
        v.layer[0] = take_layer(c, (size_t)u->cout * (u->cin / u->groups), u->cout, u->fin, true);
        v.layer[1] = take_layer(c, (size_t)u->cout * taps, u->cout, u->fout, true);
        if (u->kind == XMB) {
            v.layer[2] = take_layer(c, (size_t)u->cout * (u->cout / u->groups), u->cout, u->fout, false);
        }
    }
    const int ch = u->cout;
    v.tfa.gru = ns_float_take_gru(c, ch, 2 * ch);
    v.tfa.fc_w = ns_float_take(c, (size_t)ch * 2 * ch);
    v.tfa.fc_b = ns_float_take(c, (size_t)ch);
    v.tfa.fa[0] = ns_float_take_gru(c, CHUNK, CHUNK);
    v.tfa.fa[1] = ns_float_take_gru(c, CHUNK, CHUNK);
    v.tfa.fa_fc_w = ns_float_take(c, (size_t)CHUNK * 2 * CHUNK);
    v.tfa.fa_fc_b = ns_float_take(c, CHUNK);
    return v;
}

esp_err_t ai_engine_ulunas_weights(const void *blob, size_t bytes, ai_engine_ulunas_weights_t *w)
{
    if (blob == NULL || w == NULL || ((uintptr_t)blob & 3u) != 0) { return ESP_ERR_INVALID_ARG; }
    ns_float_cursor_t c = {.p = blob, .end = (const uint8_t *)blob + bytes, .ok = true};
    const uint32_t *head = ns_float_take(&c, 4);
    if (head == NULL) { return ESP_ERR_INVALID_SIZE; }
    if (head[0] != BLOB_MAGIC || head[1] != BLOB_VERSION) { return ESP_ERR_INVALID_VERSION; }
    w->erb_ptr = ns_float_take(&c, ERB_BANDS + 1);
    w->erb_idx = ns_float_take(&c, head[2]);
    w->erb_coef = ns_float_take(&c, head[2]);
    w->ierb_ptr = ns_float_take(&c, HIGH_BINS + 1);
    w->ierb_idx = ns_float_take(&c, head[3]);
    w->ierb_coef = ns_float_take(&c, head[3]);
    const float *scale = ns_float_take(&c, 1);
    w->scale = scale != NULL ? *scale : 0.0f;
    for (int k = 0; k < UNITS; k++) {
        w->enc[k] = take_unit(&c, &kEnc[k]);
    }
    for (int k = 0; k < 2; k++) {
        w->dual[k] = ns_float_take_dual(&c);
    }
    for (int k = 0; k < UNITS; k++) {
        w->dec[k] = take_unit(&c, &kDec[k]);
    }
    return c.ok && c.p == c.end ? ESP_OK : ESP_ERR_INVALID_SIZE;
}
