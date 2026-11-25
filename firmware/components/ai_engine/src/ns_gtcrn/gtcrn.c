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

static float exp32(float x)
{
    x = x < -kExpLimit ? -kExpLimit : (x > kExpLimit ? kExpLimit : x);
    const float n = floorf(x * kLog2e + 0.5f);
    const float r = fmaf(-n, kLn2Lo, fmaf(-n, kLn2Hi, x));
    float p = kExpPoly[0];
    for (size_t k = 1; k < sizeof(kExpPoly) / sizeof(kExpPoly[0]); k++) {
        p = fmaf(p, r, kExpPoly[k]);
    }
    return p * ldexpf(1.0f, (int)n);
}

static float sqrt32(float v)
{
    if (v <= 0.0f) { return 0.0f; }
    int e = 0;
    (void)frexpf(v, &e);
    // The mirror halves the exponent by floor division.
    float y = ldexpf(1.0f, e >= 0 ? e / 2 : -((1 - e) / 2));
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

typedef struct {
    int cout, per_in, kt, kf, stride, pad, dil, groups, fout;
} conv_shape_t;

// x is (cin, span, fin), w (cout, per_in, kt, kf); taps over the zero padding count, in (ci, t, k) order.
static void conv(const float *x, int span, int fin, const ai_engine_gtcrn_conv_t *c, conv_shape_t s,
                 act_t act, float *out)
{
    const int per_out = s.cout / s.groups;
    for (int co = 0; co < s.cout; co++) {
        const int g = co / per_out;
        const float *wc = c->w + (size_t)co * s.per_in * s.kt * s.kf;
        for (int fo = 0; fo < s.fout; fo++) {
            float acc = c->b[co];
            for (int ci = 0; ci < s.per_in; ci++) {
                for (int t = 0; t < s.kt; t++) {
                    const float *row = x + ((size_t)(g * s.per_in + ci) * span + t * s.dil) * fin;
                    const float *wk = wc + (ci * s.kt + t) * s.kf;
                    for (int k = 0; k < s.kf; k++) {
                        const int f = fo * s.stride - s.pad + k;
                        acc = fmaf(wk[k], f >= 0 && f < fin ? row[f] : 0.0f, acc);
                    }
                }
            }
            out[co * s.fout + fo] = act_of(acc, act, c->slope);
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
            for (int ci = g * per_in; ci < (g + 1) * per_in; ci++) {
                for (int k = 0; k < kf; k++) {
                    const int num = fo + pad - k;
                    if (num >= 0 && num % stride == 0 && num / stride < fin) {
                        acc = fmaf(x[ci * fin + num / stride], c->w[(ci * per_out + local) * kf + k], acc);
                    }
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
    conv(st->s, 1, F2, &blk->pc1, (conv_shape_t){CH, 3 * HALF, 1, 1, 1, 0, 1, 1, F2}, ACT_PRELU, st->h);
    for (int c = 0; c < CH; c++) {
        memcpy(cache + (c * span + frames) * F2, st->h + c * F2, F2 * sizeof(float));
    }
    conv(cache, span, F2, &blk->dw, (conv_shape_t){CH, 1, 3, 3, 1, 1, blk->dil, CH, F2}, ACT_PRELU, st->d);
    for (int c = 0; c < CH; c++) {
        memmove(cache + c * span * F2, cache + (c * span + 1) * F2, (size_t)frames * F2 * sizeof(float));
    }
    float *y = st->t;
    conv(st->d, 1, F2, &blk->pc2, (conv_shape_t){HALF, CH, 1, 1, 1, 0, 1, 1, F2}, ACT_NONE, y);
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
    conv(st->s, 1, ERB, &w->en0, (conv_shape_t){CH, 9, 1, 5, 2, 2, 1, 1, F1}, ACT_PRELU, st->o0);
    conv(st->o0, 1, F1, &w->en1, (conv_shape_t){CH, HALF, 1, 5, 2, 2, 1, 2, F2}, ACT_PRELU, st->o1);
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
