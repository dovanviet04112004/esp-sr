#include "ns_gtcrn/gtcrn.h"
#include "ns_float/ns_float.h"

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

static const float kMagEps = 0x1.197998p-40f; // gtcrn_mirror.py's MAG_EPS, bit for bit

typedef enum { ACT_NONE, ACT_PRELU, ACT_TANH } act_t;

static inline __attribute__((always_inline)) float act_of(float acc, act_t act, float slope)
{
    if (act == ACT_PRELU) { return acc > 0.0f ? acc : slope * acc; }
    return act == ACT_TANH ? tanh32(acc) : acc;
}

// A 1x1 convolution: x is (cin, nf), w (cout, cin), cout a multiple of 4; four outputs by two positions at a
// time share their loads, each output summed over ci in order.
static void pointwise(const float *x, int cin, int nf, const ai_engine_gtcrn_conv_t *c, int cout, act_t act,
                      float *out)
{
    for (int co = 0; co < cout; co += 4) {
        const float *w0 = c->w + co * cin;
        const float *w1 = w0 + cin;
        const float *w2 = w1 + cin;
        const float *w3 = w2 + cin;
        int f = 0;
        for (; f + 2 <= nf; f += 2) {
            float a0 = c->b[co];
            float a1 = c->b[co + 1];
            float a2 = c->b[co + 2];
            float a3 = c->b[co + 3];
            float b0 = a0;
            float b1 = a1;
            float b2 = a2;
            float b3 = a3;
            for (int ci = 0; ci < cin; ci++) {
                const float v = x[ci * nf + f];
                const float u = x[ci * nf + f + 1];
                a0 = fmaf(w0[ci], v, a0);
                b0 = fmaf(w0[ci], u, b0);
                a1 = fmaf(w1[ci], v, a1);
                b1 = fmaf(w1[ci], u, b1);
                a2 = fmaf(w2[ci], v, a2);
                b2 = fmaf(w2[ci], u, b2);
                a3 = fmaf(w3[ci], v, a3);
                b3 = fmaf(w3[ci], u, b3);
            }
            out[co * nf + f] = act_of(a0, act, c->slope);
            out[co * nf + f + 1] = act_of(b0, act, c->slope);
            out[(co + 1) * nf + f] = act_of(a1, act, c->slope);
            out[(co + 1) * nf + f + 1] = act_of(b1, act, c->slope);
            out[(co + 2) * nf + f] = act_of(a2, act, c->slope);
            out[(co + 2) * nf + f + 1] = act_of(b2, act, c->slope);
            out[(co + 3) * nf + f] = act_of(a3, act, c->slope);
            out[(co + 3) * nf + f + 1] = act_of(b3, act, c->slope);
        }
        for (; f < nf; f++) {
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
        int fo = 0;
        for (; fo + 2 <= fout; fo += 2) {
            float a0 = c->b[co];
            float a1 = c->b[co + 1];
            float a2 = c->b[co + 2];
            float a3 = c->b[co + 3];
            float b0 = a0;
            float b1 = a1;
            float b2 = a2;
            float b3 = a3;
            for (int ci = 0; ci < per_in; ci++) {
                const float *v = pad + (g * per_in + ci) * width + 2 * fo;
                const int at = ci * 5;
                for (int k = 0; k < 5; k++) {
                    const float p = v[k];
                    const float q = v[k + 2];
                    a0 = fmaf(w0[at + k], p, a0);
                    b0 = fmaf(w0[at + k], q, b0);
                    a1 = fmaf(w1[at + k], p, a1);
                    b1 = fmaf(w1[at + k], q, b1);
                    a2 = fmaf(w2[at + k], p, a2);
                    b2 = fmaf(w2[at + k], q, b2);
                    a3 = fmaf(w3[at + k], p, a3);
                    b3 = fmaf(w3[at + k], q, b3);
                }
            }
            out[co * fout + fo + 1] = act_of(b0, ACT_PRELU, c->slope);
            out[(co + 1) * fout + fo + 1] = act_of(b1, ACT_PRELU, c->slope);
            out[(co + 2) * fout + fo + 1] = act_of(b2, ACT_PRELU, c->slope);
            out[(co + 3) * fout + fo + 1] = act_of(b3, ACT_PRELU, c->slope);
            out[co * fout + fo] = act_of(a0, ACT_PRELU, c->slope);
            out[(co + 1) * fout + fo] = act_of(a1, ACT_PRELU, c->slope);
            out[(co + 2) * fout + fo] = act_of(a2, ACT_PRELU, c->slope);
            out[(co + 3) * fout + fo] = act_of(a3, ACT_PRELU, c->slope);
        }
        for (; fo < fout; fo++) {
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
static float dw_one(const float *w, float b, const float *const r[3], int fo)
{
    float a = b;
    for (int t = 0; t < 3; t++) {
        a = fmaf(w[3 * t], fo > 0 ? r[t][fo - 1] : 0.0f, a);
        a = fmaf(w[3 * t + 1], r[t][fo], a);
        a = fmaf(w[3 * t + 2], fo < F2 - 1 ? r[t][fo + 1] : 0.0f, a);
    }
    return a;
}

static void depthwise(const float *cache, int span, const int slot[3], const ai_engine_gtcrn_conv_t *c,
                      float *out)
{
    for (int ch = 0; ch < CH; ch++) {
        const float *w = c->w + ch * 9;
        const float *const r[3] = {cache + (ch * span + slot[0]) * F2, cache + (ch * span + slot[1]) * F2,
                                   cache + (ch * span + slot[2]) * F2};
        float *o = out + ch * F2;
        o[0] = act_of(dw_one(w, c->b[ch], r, 0), ACT_PRELU, c->slope);
        int fo = 1;
        for (; fo + 4 <= F2 - 1; fo += 4) {
            float a0 = c->b[ch];
            float a1 = a0;
            float a2 = a0;
            float a3 = a0;
            for (int t = 0; t < 3; t++) {
                for (int k = 0; k < 3; k++) {
                    const float wv = w[3 * t + k];
                    const float *v = r[t] + fo - 1 + k;
                    a0 = fmaf(wv, v[0], a0);
                    a1 = fmaf(wv, v[1], a1);
                    a2 = fmaf(wv, v[2], a2);
                    a3 = fmaf(wv, v[3], a3);
                }
            }
            o[fo] = act_of(a0, ACT_PRELU, c->slope);
            o[fo + 1] = act_of(a1, ACT_PRELU, c->slope);
            o[fo + 2] = act_of(a2, ACT_PRELU, c->slope);
            o[fo + 3] = act_of(a3, ACT_PRELU, c->slope);
        }
        for (; fo < F2; fo++) {
            o[fo] = act_of(dw_one(w, c->b[ch], r, fo), ACT_PRELU, c->slope);
        }
    }
}

// ConvTranspose2d, kernel 5, stride 2, padding 2, w (cin, per_out, 1, 5): even outputs take taps 0, 2, 4 of
// inputs half + 1, half, half - 1, odd ones taps 1, 3 of half + 1, half, those in range only.
static void deconv(const float *x, int cin, int fin, const ai_engine_gtcrn_conv_t *c, int per_out, int groups,
                   int fout, act_t act, float *out)
{
    const int per_in = cin / groups;
    for (int co = 0; co < per_out * groups; co += 2) {
        const int g = co / per_out;
        const int local = co % per_out;
        for (int fo = 0; fo < fout; fo++) {
            const int half = fo >> 1;
            float a0 = c->b[co];
            float a1 = c->b[co + 1];
            for (int ci = g * per_in; ci < (g + 1) * per_in; ci++) {
                const float *xr = x + ci * fin;
                const float *w0 = c->w + (ci * per_out + local) * 5;
                const float *w1 = w0 + 5;
                if ((fo & 1) == 0) {
                    if (half + 1 < fin) {
                        a0 = fmaf(xr[half + 1], w0[0], a0);
                        a1 = fmaf(xr[half + 1], w1[0], a1);
                    }
                    a0 = fmaf(xr[half], w0[2], a0);
                    a1 = fmaf(xr[half], w1[2], a1);
                    if (half >= 1) {
                        a0 = fmaf(xr[half - 1], w0[4], a0);
                        a1 = fmaf(xr[half - 1], w1[4], a1);
                    }
                } else {
                    a0 = fmaf(xr[half + 1], w0[1], a0);
                    a1 = fmaf(xr[half + 1], w1[1], a1);
                    a0 = fmaf(xr[half], w0[3], a0);
                    a1 = fmaf(xr[half], w1[3], a1);
                }
            }
            out[co * fout + fo] = act_of(a0, act, c->slope);
            out[(co + 1) * fout + fo] = act_of(a1, act, c->slope);
        }
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

// cache is a ring of span frames per channel, oldest at slot *head: this hop's frame goes in after the
// newest, and the oldest drops out by moving head, never the frames.
static void run_block(const ai_engine_gtcrn_block_t *blk, ai_engine_gtcrn_state_t *st, float *cache,
                      uint8_t *head, float *tra, const float *x, float *out)
{
    const int frames = 2 * blk->dil;
    const int span = frames + 1;
    const int slot[3] = {*head, (*head + blk->dil) % span, (*head + frames) % span};
    sfe(x, HALF, F2, st->s);
    pointwise(st->s, 3 * HALF, F2, &blk->pc1, CH, ACT_PRELU, st->h);
    for (int c = 0; c < CH; c++) {
        memcpy(cache + (c * span + slot[2]) * F2, st->h + c * F2, F2 * sizeof(float));
    }
    depthwise(cache, span, slot, &blk->dw, st->d);
    *head = (uint8_t)((*head + 1) % span);
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
    sigmoid_n(at, HALF);
    for (int c = 0; c < HALF; c++) {
        const float a = at[c];
        for (int i = 0; i < F2; i++) {
            out[2 * c * F2 + i] = y[c * F2 + i] * a;
            out[(2 * c + 1) * F2 + i] = x[(HALF + c) * F2 + i];
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
    memset(st->head, 0, sizeof(st->head));
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
    banded(st->x, 3, BINS, LOW, w->erb_ptr, w->erb_idx, w->erb_coef, ERB_BANDS, ERB, st->e);
    sfe(st->e, 3, ERB, st->s);
    conv_k5s2(st->s, ERB, &w->en0, CH, 9, 1, F1, st->pad, st->o0);
    conv_k5s2(st->o0, F1, &w->en1, CH, HALF, 2, F2, st->pad, st->o1);
    const float *in = st->o1;
    float *cache = st->cache;
    for (int k = 0; k < AI_ENGINE_GTCRN_BLOCKS; k++) {
        run_block(&w->enc[k], st, cache, &st->head[k], st->tra[k], in, st->skip[k]);
        cache += CH * (2 * w->enc[k].dil + 1) * F2;
        in = st->skip[k];
    }
    memcpy(st->y, in, PLANE * sizeof(float));
    const ns_float_dual_work_t work = {.t = st->t,
                                       .y = st->d,
                                       .u = st->u,
                                       .hn = st->hn,
                                       .gi = st->gi,
                                       .gh = st->gh,
                                       .rz = st->rz,
                                       .nn = st->nn};
    for (int k = 0; k < 2; k++) {
        run_dual(&w->dual[k], &work, st->inter[k], st->y);
    }
    for (int k = 0; k < AI_ENGINE_GTCRN_BLOCKS; k++) {
        add(st->y, st->skip[AI_ENGINE_GTCRN_BLOCKS - 1 - k], st->in, PLANE);
        run_block(&w->dec[k], st, cache, &st->head[AI_ENGINE_GTCRN_BLOCKS + k],
                  st->tra[AI_ENGINE_GTCRN_BLOCKS + k], st->in, st->y);
        cache += CH * (2 * w->dec[k].dil + 1) * F2;
    }
    add(st->y, st->o1, st->in, PLANE);
    deconv(st->in, CH, F2, &w->de3, HALF, 2, F1, ACT_PRELU, st->m1);
    add(st->m1, st->o0, st->m1, CH * F1);
    deconv(st->m1, CH, F1, &w->de4, 2, 1, ERB, ACT_TANH, st->m2);
    banded(st->m2, 2, ERB, LOW, w->ierb_ptr, w->ierb_idx, w->ierb_coef, HIGH_BINS, BINS, st->mask);
    for (int i = 0; i < BINS; i++) {
        const float mr = st->mask[i];
        const float mi = st->mask[BINS + i];
        const float g = sqrt32(fmaf(mr, mr, mi * mi));
        gain[i] = g < 1.0f ? g : 1.0f;
    }
}

static ai_engine_gtcrn_conv_t take_conv(ns_float_cursor_t *c, size_t weights, size_t outs, bool slope)
{
    ai_engine_gtcrn_conv_t v = {.w = ns_float_take(c, weights), .b = ns_float_take(c, outs), .slope = 0.0f};
    const float *s = slope ? ns_float_take(c, 1) : NULL;
    v.slope = s != NULL ? *s : 0.0f;
    return v;
}

static ai_engine_gtcrn_block_t take_block(ns_float_cursor_t *c, int dil)
{
    ai_engine_gtcrn_block_t b = {.dil = dil};
    b.pc1 = take_conv(c, (size_t)CH * 3 * HALF, CH, true);
    b.dw = take_conv(c, (size_t)CH * 9, CH, true);
    b.pc2 = take_conv(c, (size_t)HALF * CH, HALF, false);
    b.att = ns_float_take_gru(c, HALF, 2 * HALF);
    b.att_w = ns_float_take(c, (size_t)HALF * 2 * HALF);
    b.att_b = ns_float_take(c, HALF);
    return b;
}

esp_err_t ai_engine_gtcrn_weights(const void *blob, size_t bytes, ai_engine_gtcrn_weights_t *w)
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
    w->en0 = take_conv(&c, (size_t)CH * 9 * 5, CH, true);
    w->en1 = take_conv(&c, (size_t)CH * HALF * 5, CH, true);
    static const int kDil[AI_ENGINE_GTCRN_BLOCKS] = {1, 2, 5};
    for (int k = 0; k < AI_ENGINE_GTCRN_BLOCKS; k++) {
        w->enc[k] = take_block(&c, kDil[k]);
    }
    for (int k = 0; k < 2; k++) {
        w->dual[k] = ns_float_take_dual(&c);
    }
    for (int k = 0; k < AI_ENGINE_GTCRN_BLOCKS; k++) {
        w->dec[k] = take_block(&c, kDil[AI_ENGINE_GTCRN_BLOCKS - 1 - k]);
    }
    w->de3 = take_conv(&c, (size_t)CH * HALF * 5, CH, true);
    w->de4 = take_conv(&c, (size_t)CH * 2 * 5, 2, true);
    return c.ok && c.p == c.end ? ESP_OK : ESP_ERR_INVALID_SIZE;
}
