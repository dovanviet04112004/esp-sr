#include "ns_gtcrn/gtcrn.h"

#include <math.h>
#include <stdbool.h>
#include <string.h>

#define BINS AI_ENGINE_GTCRN_BINS
#define LOW AI_ENGINE_GTCRN_LOW
#define ERB AI_ENGINE_GTCRN_ERB
#define F1 AI_ENGINE_GTCRN_F1
#define F2 AI_ENGINE_GTCRN_F2
#define CH AI_ENGINE_GTCRN_CH
#define HALF AI_ENGINE_GTCRN_HALF
#define PLANE AI_ENGINE_GTCRN_PLANE
#define ERB_BANDS (ERB - LOW)
#define HIGH_BINS (BINS - LOW)
#define BLOB_MAGIC 0x31435447u // "GTC1"
#define BLOB_VERSION 1u
#define SQRT_STEPS 6

// The mirror's float32 constants, bit for bit (gtcrn_mirror.py).
static const float kLog2e = 0x1.715476p+0f;
static const float kLn2Hi = 0x1.62e4p-1f;
static const float kLn2Lo = 0x1.7f7d1cp-20f;
static const float kExpLimit = 80.0f;
static const float kMagEps = 0x1.197998p-40f;
static const float kLnEps = 0x1.5798eep-27f;
static const float kExpPoly[] = {
    0x1.6c16c2p-10f, 0x1.111112p-7f, 0x1.555556p-5f, 0x1.555556p-3f, 0.5f, 1.0f, 1.0f};

typedef enum { ACT_NONE, ACT_PRELU, ACT_TANH } act_t;

static float pow2i(int n)
{
    const union {
        uint32_t u;
        float f;
    } v = {.u = (uint32_t)(127 + n) << 23};
    return v.f;
}

static float exp32(float x)
{
    x = x < -kExpLimit ? -kExpLimit : (x > kExpLimit ? kExpLimit : x);
    const float t = x * kLog2e + 0.5f;
    int i = (int)t;
    if ((float)i > t) { i--; }
    const float n = (float)i;
    const float r = fmaf(-n, kLn2Lo, fmaf(-n, kLn2Hi, x));
    float p = kExpPoly[0];
    for (size_t k = 1; k < sizeof(kExpPoly) / sizeof(kExpPoly[0]); k++) {
        p = fmaf(p, r, kExpPoly[k]);
    }
    return p * pow2i(i);
}

static float sqrt32(float v)
{
    if (v <= 0.0f) { return 0.0f; }
    const union {
        float f;
        uint32_t u;
    } bits = {.f = v};
    int e = (int)((bits.u >> 23) & 0xffu) - 126;
    if (e == -126) { (void)frexpf(v, &e); }
    // The mirror halves the exponent by floor division.
    float y = pow2i(e >= 0 ? e / 2 : -((1 - e) / 2));
    for (int i = 0; i < SQRT_STEPS; i++) {
        y = 0.5f * (y + v / y);
    }
    return y;
}

static float sigmoid32(float v)
{
    return 1.0f / (1.0f + exp32(-v));
}

static float tanh32(float v)
{
    return 1.0f - 2.0f / (exp32(2.0f * v) + 1.0f);
}

static float act_of(float acc, act_t act, float slope)
{
    if (act == ACT_PRELU) { return acc > 0.0f ? acc : slope * acc; }
    return act == ACT_TANH ? tanh32(acc) : acc;
}

// A 1x1 convolution: x is (cin, nf), w (cout, cin); four outputs at a time, each summed over ci in order.
static void pointwise(const float *x, int cin, int nf, const ai_engine_gtcrn_conv_t *c, int cout, act_t act,
                      float *out)
{
    int co = 0;
    for (; co + 4 <= cout; co += 4) {
        const float *w0 = c->w + co * cin;
        const float *w1 = w0 + cin;
        const float *w2 = w1 + cin;
        const float *w3 = w2 + cin;
        for (int f = 0; f < nf; f++) {
            float a0 = c->b[co];
            float a1 = c->b[co + 1];
            float a2 = c->b[co + 2];
            float a3 = c->b[co + 3];
            for (int ci = 0; ci < cin; ci++) {
                const float v = x[ci * nf + f];
                a0 = fmaf(w0[ci], v, a0);
                a1 = fmaf(w1[ci], v, a1);
                a2 = fmaf(w2[ci], v, a2);
                a3 = fmaf(w3[ci], v, a3);
            }
            out[co * nf + f] = act_of(a0, act, c->slope);
            out[(co + 1) * nf + f] = act_of(a1, act, c->slope);
            out[(co + 2) * nf + f] = act_of(a2, act, c->slope);
            out[(co + 3) * nf + f] = act_of(a3, act, c->slope);
        }
    }
    for (; co < cout; co++) {
        for (int f = 0; f < nf; f++) {
            float a = c->b[co];
            for (int ci = 0; ci < cin; ci++) {
                a = fmaf(c->w[co * cin + ci], x[ci * nf + f], a);
            }
            out[co * nf + f] = act_of(a, act, c->slope);
        }
    }
}

// Kernel 5, stride 2, padding 2 along frequency: each input row copied once between zeros into pad, so the
// taps over the padding are real zeros, summed in (ci, k) order like every other tap.
static void conv_k5s2(const float *x, int fin, const ai_engine_gtcrn_conv_t *c, int cout, int per_in,
                      int groups, int fout, float *pad, float *out)
{
    const int width = fin + 5;
    for (int r = 0; r < per_in * groups; r++) {
        float *row = pad + r * width;
        row[0] = row[1] = 0.0f;
        memcpy(row + 2, x + r * fin, (size_t)fin * sizeof(float));
        row[fin + 2] = row[fin + 3] = row[fin + 4] = 0.0f;
    }
    const int per_out = cout / groups;
    for (int co = 0; co < cout; co += 4) {
        const int g = co / per_out;
        const float *w0 = c->w + co * per_in * 5;
        const float *w1 = w0 + per_in * 5;
        const float *w2 = w1 + per_in * 5;
        const float *w3 = w2 + per_in * 5;
        for (int fo = 0; fo < fout; fo++) {
            float a0 = c->b[co];
            float a1 = c->b[co + 1];
            float a2 = c->b[co + 2];
            float a3 = c->b[co + 3];
            for (int ci = 0; ci < per_in; ci++) {
                const float *v = pad + (g * per_in + ci) * width + 2 * fo;
                const int at = ci * 5;
                for (int k = 0; k < 5; k++) {
                    a0 = fmaf(w0[at + k], v[k], a0);
                    a1 = fmaf(w1[at + k], v[k], a1);
                    a2 = fmaf(w2[at + k], v[k], a2);
                    a3 = fmaf(w3[at + k], v[k], a3);
                }
            }
            out[co * fout + fo] = act_of(a0, ACT_PRELU, c->slope);
            out[(co + 1) * fout + fo] = act_of(a1, ACT_PRELU, c->slope);
            out[(co + 2) * fout + fo] = act_of(a2, ACT_PRELU, c->slope);
            out[(co + 3) * fout + fo] = act_of(a3, ACT_PRELU, c->slope);
        }
    }
}

// The depthwise 3x3 over a block's cache (CH, span, F2), dilated in time; the edge taps are zeros, kept in
// order.
static void depthwise(const float *cache, int span, int dil, const ai_engine_gtcrn_conv_t *c, float *out)
{
    for (int ch = 0; ch < CH; ch++) {
        const float *w = c->w + ch * 9;
        const float *r0 = cache + ch * span * F2;
        const float *r1 = r0 + dil * F2;
        const float *r2 = r1 + dil * F2;
        for (int fo = 0; fo < F2; fo++) {
            const float l0 = fo > 0 ? r0[fo - 1] : 0.0f;
            const float l1 = fo > 0 ? r1[fo - 1] : 0.0f;
            const float l2 = fo > 0 ? r2[fo - 1] : 0.0f;
            const float h0 = fo < F2 - 1 ? r0[fo + 1] : 0.0f;
            const float h1 = fo < F2 - 1 ? r1[fo + 1] : 0.0f;
            const float h2 = fo < F2 - 1 ? r2[fo + 1] : 0.0f;
            float a = c->b[ch];
            a = fmaf(w[0], l0, a);
            a = fmaf(w[1], r0[fo], a);
            a = fmaf(w[2], h0, a);
            a = fmaf(w[3], l1, a);
            a = fmaf(w[4], r1[fo], a);
            a = fmaf(w[5], h1, a);
            a = fmaf(w[6], l2, a);
            a = fmaf(w[7], r2[fo], a);
            a = fmaf(w[8], h2, a);
            out[ch * F2 + fo] = act_of(a, ACT_PRELU, c->slope);
        }
    }
}

// A ConvTranspose2d along frequency: w is (cin, per_out, 1, kf); only the taps on real input positions count.
static void deconv(const float *x, int cin, int fin, const ai_engine_gtcrn_conv_t *c, int per_out, int kf,
                   int stride, int pad, int groups, int fout, act_t act, float *out)
{
    const int per_in = cin / groups;
    for (int co = 0; co < per_out * groups; co++) {
        const int g = co / per_out;
        const int local = co % per_out;
        for (int fo = 0; fo < fout; fo++) {
            float acc = c->b[co];
            const int first = (fo + pad) % stride;
            for (int ci = g * per_in; ci < (g + 1) * per_in; ci++) {
                const float *wk = c->w + (ci * per_out + local) * kf;
                for (int k = first; k < kf; k += stride) {
                    const int fi = (fo + pad - k) / stride;
                    if (fi >= 0 && fi < fin) { acc = fmaf(x[ci * fin + fi], wk[k], acc); }
                }
            }
            out[co * fout + fo] = act_of(acc, act, c->slope);
        }
    }
}

static void gru_step(const ai_engine_gtcrn_gru_t *g, int in, int hid, const float *x, const float *h,
                     float *out)
{
    float gi[3 * 2 * HALF];
    float gh[3 * 2 * HALF];
    for (int r = 0; r < 3 * hid; r++) {
        float a = g->bih[r];
        float c = g->bhh[r];
        for (int i = 0; i < in; i++) {
            a = fmaf(g->wih[r * in + i], x[i], a);
        }
        for (int j = 0; j < hid; j++) {
            c = fmaf(g->whh[r * hid + j], h[j], c);
        }
        gi[r] = a;
        gh[r] = c;
    }
    for (int j = 0; j < hid; j++) {
        const float rg = sigmoid32(gi[j] + gh[j]);
        const float zg = sigmoid32(gi[hid + j] + gh[hid + j]);
        const float n = tanh32(fmaf(rg, gh[2 * hid + j], gi[2 * hid + j]));
        out[j] = fmaf(zg, h[j] - n, n);
    }
}

static void dense(const float *w, const float *b, int in, int out_n, const float *x, float *out)
{
    for (int o = 0; o < out_n; o++) {
        float a = b[o];
        for (int i = 0; i < in; i++) {
            a = fmaf(w[o * in + i], x[i], a);
        }
        out[o] = a;
    }
}

static void layer_norm(float *x, const float *w, const float *b, int n)
{
    float s = 0.0f;
    for (int i = 0; i < n; i++) {
        s = s + x[i];
    }
    const float mean = s / (float)n;
    float v = 0.0f;
    for (int i = 0; i < n; i++) {
        const float d = x[i] - mean;
        v = fmaf(d, d, v);
    }
    const float inv = 1.0f / sqrt32(v / (float)n + kLnEps);
    for (int i = 0; i < n; i++) {
        x[i] = fmaf((x[i] - mean) * inv, w[i], b[i]);
    }
}

static void sfe(const float *x, int ch, int f, float *out)
{
    for (int c = 0; c < ch; c++) {
        for (int j = 0; j < 3; j++) {
            for (int i = 0; i < f; i++) {
                const int src = i + j - 1;
                out[(c * 3 + j) * f + i] = src >= 0 && src < f ? x[c * f + src] : 0.0f;
            }
        }
    }
}

static void banded(const float *x, int ch, int fin, const int32_t *ptr, const int32_t *idx, const float *coef,
                   int bands, int fout, float *out)
{
    for (int c = 0; c < ch; c++) {
        memcpy(out + c * fout, x + c * fin, LOW * sizeof(float));
        for (int j = 0; j < bands; j++) {
            float a = 0.0f;
            for (int p = ptr[j]; p < ptr[j + 1]; p++) {
                a = fmaf(coef[p], x[c * fin + LOW + idx[p]], a);
            }
            out[c * fout + LOW + j] = a;
        }
    }
}

static float mean_square(const float *v, int n)
{
    float a = 0.0f;
    for (int i = 0; i < n; i++) {
        a = fmaf(v[i], v[i], a);
    }
    return a / (float)n;
}

static void run_block(const ai_engine_gtcrn_block_t *blk, ai_engine_gtcrn_state_t *st, float *cache,
                      float *tra, const float *x, float *out)
{
    const int frames = 2 * blk->dil;
    const int span = frames + 1;
    sfe(x, HALF, F2, st->s);
    pointwise(st->s, 3 * HALF, F2, &blk->pc1, CH, ACT_PRELU, st->h);
    for (int c = 0; c < CH; c++) {
        memcpy(cache + (c * span + frames) * F2, st->h + c * F2, F2 * sizeof(float));
    }
    depthwise(cache, span, blk->dil, &blk->dw, st->d);
    for (int c = 0; c < CH; c++) {
        memmove(cache + c * span * F2, cache + (c * span + 1) * F2, (size_t)frames * F2 * sizeof(float));
    }
    float *y = st->t;
    pointwise(st->d, CH, F2, &blk->pc2, HALF, ACT_NONE, y);
    float z[HALF];
    float fresh[2 * HALF];
    float at[HALF];
    for (int c = 0; c < HALF; c++) {
        z[c] = mean_square(y + c * F2, F2);
    }
    gru_step(&blk->att, HALF, 2 * HALF, z, tra, fresh);
    memcpy(tra, fresh, sizeof(fresh));
    dense(blk->att_w, blk->att_b, 2 * HALF, HALF, tra, at);
    for (int c = 0; c < HALF; c++) {
        const float a = sigmoid32(at[c]);
        for (int i = 0; i < F2; i++) {
            out[2 * c * F2 + i] = y[c * F2 + i] * a;
            out[(2 * c + 1) * F2 + i] = x[(HALF + c) * F2 + i];
        }
    }
}

static void run_dual(const ai_engine_gtcrn_dual_t *dp, ai_engine_gtcrn_state_t *st, float *inter, float *x)
{
    float *t = st->t;
    float *y = st->d;
    float *u = st->u;
    for (int f = 0; f < F2; f++) {
        for (int c = 0; c < CH; c++) {
            t[f * CH + c] = x[c * F2 + f];
        }
    }
    const int quarter = HALF / 2;
    for (int g = 0; g < 2; g++) {
        for (int dir = 0; dir < 2; dir++) {
            float h[HALF / 2] = {0};
            float fresh[HALF / 2];
            for (int n = 0; n < F2; n++) {
                const int f = dir == 0 ? n : F2 - 1 - n;
                gru_step(&dp->intra[g][dir], HALF, quarter, t + f * CH + g * HALF, h, fresh);
                memcpy(h, fresh, sizeof(h));
                memcpy(y + f * CH + g * HALF + dir * quarter, h, sizeof(h));
            }
        }
    }
    for (int f = 0; f < F2; f++) {
        dense(dp->intra_fc_w, dp->intra_fc_b, CH, CH, y + f * CH, u + f * CH);
    }
    layer_norm(u, dp->intra_ln_w, dp->intra_ln_b, PLANE);
    for (int i = 0; i < PLANE; i++) {
        t[i] = t[i] + u[i];
    }
    for (int f = 0; f < F2; f++) {
        for (int g = 0; g < 2; g++) {
            gru_step(&dp->inter[g], HALF, HALF, t + f * CH + g * HALF, inter + f * CH + g * HALF,
                     st->hn + f * CH + g * HALF);
        }
    }
    memcpy(inter, st->hn, PLANE * sizeof(float));
    for (int f = 0; f < F2; f++) {
        dense(dp->inter_fc_w, dp->inter_fc_b, CH, CH, st->hn + f * CH, u + f * CH);
    }
    layer_norm(u, dp->inter_ln_w, dp->inter_ln_b, PLANE);
    for (int f = 0; f < F2; f++) {
        for (int c = 0; c < CH; c++) {
            x[c * F2 + f] = t[f * CH + c] + u[f * CH + c];
        }
    }
}

static void add(const float *a, const float *b, float *out, int n)
{
    for (int i = 0; i < n; i++) {
        out[i] = a[i] + b[i];
    }
}

void ai_engine_gtcrn_reset(ai_engine_gtcrn_state_t *st)
{
    memset(st->cache, 0, sizeof(st->cache));
    memset(st->tra, 0, sizeof(st->tra));
    memset(st->inter, 0, sizeof(st->inter));
}

void ai_engine_gtcrn_step(const ai_engine_gtcrn_weights_t *w, ai_engine_gtcrn_state_t *st, const float *bins,
                          float *gain)
{
    for (int i = 0; i < BINS; i++) {
        const float re = bins[2 * i];
        const float im = bins[2 * i + 1];
        st->x[i] = sqrt32(fmaf(re, re, fmaf(im, im, kMagEps)));
        st->x[BINS + i] = re;
        st->x[2 * BINS + i] = im;
    }
    banded(st->x, 3, BINS, w->erb_ptr, w->erb_idx, w->erb_coef, ERB_BANDS, ERB, st->e);
    sfe(st->e, 3, ERB, st->s);
    conv_k5s2(st->s, ERB, &w->en0, CH, 9, 1, F1, st->pad, st->o0);
    conv_k5s2(st->o0, F1, &w->en1, CH, HALF, 2, F2, st->pad, st->o1);
    const float *in = st->o1;
    for (int k = 0; k < AI_ENGINE_GTCRN_BLOCKS; k++) {
        run_block(&w->enc[k], st, st->cache[k], st->tra[k], in, st->skip[k]);
        in = st->skip[k];
    }
    memcpy(st->y, in, PLANE * sizeof(float));
    for (int k = 0; k < 2; k++) {
        run_dual(&w->dual[k], st, st->inter[k], st->y);
    }
    for (int k = 0; k < AI_ENGINE_GTCRN_BLOCKS; k++) {
        add(st->y, st->skip[AI_ENGINE_GTCRN_BLOCKS - 1 - k], st->in, PLANE);
        run_block(&w->dec[k], st, st->cache[AI_ENGINE_GTCRN_BLOCKS + k], st->tra[AI_ENGINE_GTCRN_BLOCKS + k],
                  st->in, st->y);
    }
    add(st->y, st->o1, st->in, PLANE);
    deconv(st->in, CH, F2, &w->de3, HALF, 5, 2, 2, 2, F1, ACT_PRELU, st->m1);
    add(st->m1, st->o0, st->m1, CH * F1);
    deconv(st->m1, CH, F1, &w->de4, 2, 5, 2, 2, 1, ERB, ACT_TANH, st->m2);
    banded(st->m2, 2, ERB, w->ierb_ptr, w->ierb_idx, w->ierb_coef, HIGH_BINS, BINS, st->mask);
    for (int i = 0; i < BINS; i++) {
        const float mr = st->mask[i];
        const float mi = st->mask[BINS + i];
        const float g = sqrt32(fmaf(mr, mr, mi * mi));
        gain[i] = g < 1.0f ? g : 1.0f;
    }
}

typedef struct {
    const uint8_t *p;
    const uint8_t *end;
    bool ok;
} cursor_t;

static const void *take(cursor_t *c, size_t count)
{
    const size_t bytes = count * sizeof(float);
    if (!c->ok || (size_t)(c->end - c->p) < bytes) {
        c->ok = false;
        return NULL;
    }
    const void *at = c->p;
    c->p += bytes;
    return at;
}

static ai_engine_gtcrn_conv_t take_conv(cursor_t *c, size_t weights, size_t outs, bool slope)
{
    ai_engine_gtcrn_conv_t v = {.w = take(c, weights), .b = take(c, outs), .slope = 0.0f};
    const float *s = slope ? take(c, 1) : NULL;
    v.slope = s != NULL ? *s : 0.0f;
    return v;
}

static ai_engine_gtcrn_gru_t take_gru(cursor_t *c, int in, int hid)
{
    return (ai_engine_gtcrn_gru_t){.wih = take(c, (size_t)3 * hid * in),
                                   .whh = take(c, (size_t)3 * hid * hid),
                                   .bih = take(c, (size_t)3 * hid),
                                   .bhh = take(c, (size_t)3 * hid)};
}

static ai_engine_gtcrn_block_t take_block(cursor_t *c, int dil)
{
    ai_engine_gtcrn_block_t b = {.dil = dil};
    b.pc1 = take_conv(c, (size_t)CH * 3 * HALF, CH, true);
    b.dw = take_conv(c, (size_t)CH * 9, CH, true);
    b.pc2 = take_conv(c, (size_t)HALF * CH, HALF, false);
    b.att = take_gru(c, HALF, 2 * HALF);
    b.att_w = take(c, (size_t)HALF * 2 * HALF);
    b.att_b = take(c, HALF);
    return b;
}

static ai_engine_gtcrn_dual_t take_dual(cursor_t *c)
{
    ai_engine_gtcrn_dual_t d;
    for (int g = 0; g < 2; g++) {
        d.intra[g][0] = take_gru(c, HALF, HALF / 2);
        d.intra[g][1] = take_gru(c, HALF, HALF / 2);
    }
    d.intra_fc_w = take(c, CH * CH);
    d.intra_fc_b = take(c, CH);
    d.intra_ln_w = take(c, PLANE);
    d.intra_ln_b = take(c, PLANE);
    for (int g = 0; g < 2; g++) {
        d.inter[g] = take_gru(c, HALF, HALF);
    }
    d.inter_fc_w = take(c, CH * CH);
    d.inter_fc_b = take(c, CH);
    d.inter_ln_w = take(c, PLANE);
    d.inter_ln_b = take(c, PLANE);
    return d;
}

esp_err_t ai_engine_gtcrn_weights(const void *blob, size_t bytes, ai_engine_gtcrn_weights_t *w)
{
    if (blob == NULL || w == NULL || ((uintptr_t)blob & 3u) != 0) { return ESP_ERR_INVALID_ARG; }
    cursor_t c = {.p = blob, .end = (const uint8_t *)blob + bytes, .ok = true};
    const uint32_t *head = take(&c, 4);
    if (head == NULL) { return ESP_ERR_INVALID_SIZE; }
    if (head[0] != BLOB_MAGIC || head[1] != BLOB_VERSION) { return ESP_ERR_INVALID_VERSION; }
    w->erb_ptr = take(&c, ERB_BANDS + 1);
    w->erb_idx = take(&c, head[2]);
    w->erb_coef = take(&c, head[2]);
    w->ierb_ptr = take(&c, HIGH_BINS + 1);
    w->ierb_idx = take(&c, head[3]);
    w->ierb_coef = take(&c, head[3]);
    w->en0 = take_conv(&c, (size_t)CH * 9 * 5, CH, true);
    w->en1 = take_conv(&c, (size_t)CH * HALF * 5, CH, true);
    static const int kDil[AI_ENGINE_GTCRN_BLOCKS] = {1, 2, 5};
    for (int k = 0; k < AI_ENGINE_GTCRN_BLOCKS; k++) {
        w->enc[k] = take_block(&c, kDil[k]);
    }
    for (int k = 0; k < 2; k++) {
        w->dual[k] = take_dual(&c);
    }
    for (int k = 0; k < AI_ENGINE_GTCRN_BLOCKS; k++) {
        w->dec[k] = take_block(&c, kDil[AI_ENGINE_GTCRN_BLOCKS - 1 - k]);
    }
    w->de3 = take_conv(&c, (size_t)CH * HALF * 5, CH, true);
    w->de4 = take_conv(&c, (size_t)CH * 2 * 5, 2, true);
    return c.ok && c.p == c.end ? ESP_OK : ESP_ERR_INVALID_SIZE;
}
