/** The float32 steps ns_gtcrn and ns_ulunas share (KEHOACH 3.9, ADR-0047, ADR-0048): exp, reciprocal and
 * square root without libm or a division, GRU steps, dense layers, LayerNorm, sparse ERB banks and the
 * dual-path GRU block, each as srpipe.tasks.ns.gtcrn_mirror takes it, so both nets match their mirrors bit
 * for bit. Header only: each net compiles it into its own file, where constant sizes let the loops unroll and
 * size the GRU's arrays.
 */
#pragma once

#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "ns_float/ns_float_types.h"

// The mirror's float32 constants, bit for bit (gtcrn_mirror.py).
static const float kNsfLog2e = 0x1.715476p+0f;
static const float kNsfLn2Hi = 0x1.62e4p-1f;
static const float kNsfLn2Lo = 0x1.7f7d1cp-20f;
static const float kNsfExpLimit = 80.0f;
static const float kNsfLnEps = 0x1.5798eep-27f;
static const float kNsfRecipC0 = 0x1.69696ap+1f;
static const float kNsfRecipC1 = 0x1.e1e1e2p+0f;
static const float kNsfRsqrtLow0 = 0x1.d413ccp+0f;
static const float kNsfRsqrtLow1 = 0x1.a82798p-1f;
static const float kNsfRsqrtHigh0 = 0x1.4afb0cp+0f;
static const float kNsfRsqrtHigh1 = 0x1.2bec32p-2f;
static const float kNsfTinySquare = 0x1p-100f;
static const float kNsfExpPoly[] = {
    0x1.6c16c2p-10f, 0x1.111112p-7f, 0x1.555556p-5f, 0x1.555556p-3f, 0.5f, 1.0f, 1.0f};

// Every small numeric step below is inlined: called at each gate, a call costs as much as the step.

static inline __attribute__((always_inline)) float pow2i(int n)
{
    const union {
        uint32_t u;
        float f;
    } v = {.u = (uint32_t)(127 + n) << 23};
    return v.f;
}

static inline __attribute__((always_inline)) float exp32(float x)
{
    x = x < -kNsfExpLimit ? -kNsfExpLimit : (x > kNsfExpLimit ? kNsfExpLimit : x);
    const float t = x * kNsfLog2e + 0.5f;
    int i = (int)t;
    if ((float)i > t) { i--; }
    const float n = (float)i;
    const float r = fmaf(-n, kNsfLn2Lo, fmaf(-n, kNsfLn2Hi, x));
    float p = kNsfExpPoly[0];
    for (size_t k = 1; k < sizeof(kNsfExpPoly) / sizeof(kNsfExpPoly[0]); k++) {
        p = fmaf(p, r, kNsfExpPoly[k]);
    }
    return p * pow2i(i);
}

// A normal v > 0 as m 2^e with m in [0.5, 1), as frexpf gives it.
static inline __attribute__((always_inline)) float split(float v, int *e)
{
    union {
        float f;
        uint32_t u;
    } bits = {.f = v};
    *e = (int)((bits.u >> 23) & 0xffu) - 126;
    bits.u = (bits.u & 0x807fffffu) | (126u << 23);
    return bits.f;
}

static inline __attribute__((always_inline)) float recip32(float d)
{
    int e = 0;
    const float m = split(d, &e);
    float y = fmaf(-kNsfRecipC1, m, kNsfRecipC0);
    for (int i = 0; i < NS_FLOAT_NEWTON_STEPS; i++) {
        y = fmaf(y, fmaf(-m, y, 1.0f), y);
    }
    return y * pow2i(-e);
}

static inline __attribute__((always_inline)) float rsqrt32(float v)
{
    int e = 0;
    float m = split(v, &e);
    if (e % 2 != 0) {
        m = m * 2.0f;
        e -= 1;
    }
    const bool low = m < 1.0f;
    float y = fmaf(-(low ? kNsfRsqrtLow1 : kNsfRsqrtHigh1), m, low ? kNsfRsqrtLow0 : kNsfRsqrtHigh0);
    const float h = 0.5f * m;
    for (int i = 0; i < NS_FLOAT_NEWTON_STEPS; i++) {
        y = y * fmaf(-h, y * y, 1.5f);
    }
    return y * pow2i(-(e / 2));
}

static inline __attribute__((always_inline)) float sqrt32(float v)
{
    return v <= kNsfTinySquare ? 0.0f : v * rsqrt32(v);
}

static inline __attribute__((always_inline)) float sigmoid32(float v)
{
    return recip32(1.0f + exp32(-v));
}

static inline __attribute__((always_inline)) float tanh32(float v)
{
    return 1.0f - 2.0f * recip32(exp32(2.0f * v) + 1.0f);
}

// Rows of one GRU's gates: out[r] = bias[r] + w[r] . v over n inputs in order, four rows at a time.
static inline __attribute__((always_inline)) void gate_rows(const float *w, const float *bias, int rows,
                                                            int n, const float *v, float *out)
{
    int r = 0;
    for (; r + 4 <= rows; r += 4) {
        const float *w0 = w + r * n;
        float a0 = bias[r];
        float a1 = bias[r + 1];
        float a2 = bias[r + 2];
        float a3 = bias[r + 3];
#pragma GCC unroll 16
        for (int i = 0; i < n; i++) {
            const float x = v[i];
            a0 = fmaf(w0[i], x, a0);
            a1 = fmaf(w0[n + i], x, a1);
            a2 = fmaf(w0[2 * n + i], x, a2);
            a3 = fmaf(w0[3 * n + i], x, a3);
        }
        out[r] = a0;
        out[r + 1] = a1;
        out[r + 2] = a2;
        out[r + 3] = a3;
    }
    const int tail = rows % 4;
    for (int k = 0; k < tail; k++) {
        const float *wr = w + (size_t)(r + k) * (size_t)n;
        float a = bias[r + k];
        for (int i = 0; i < n; i++) {
            a = fmaf(wr[i], v[i], a);
        }
        out[r + k] = a;
    }
}

// count GRUs of hid units, their gate rows in gi and gh, (3 hid) apart: h' = n + z (h - n) into out, gates as
// gru_step takes them, each kind for every GRU in one loop.
static inline void gates(int count, int hid, const float *gi, const float *gh, const float *h, int h_stride,
                         float *rz, float *n, float *out, int out_stride)
{
    for (int q = 0; q < count; q++) {
        for (int j = 0; j < 2 * hid; j++) {
            rz[q * 2 * hid + j] = sigmoid32(gi[q * 3 * hid + j] + gh[q * 3 * hid + j]);
        }
    }
    for (int q = 0; q < count; q++) {
        for (int j = 0; j < hid; j++) {
            const int at = q * 3 * hid + 2 * hid + j;
            n[q * hid + j] = tanh32(fmaf(rz[q * 2 * hid + j], gh[at], gi[at]));
        }
    }
    for (int q = 0; q < count; q++) {
        for (int j = 0; j < hid; j++) {
            const float nj = n[q * hid + j];
            out[q * out_stride + j] = fmaf(rz[q * 2 * hid + hid + j], h[q * h_stride + j] - nj, nj);
        }
    }
}

// Inlined at every call, whose sizes are constants, so its loops unroll and its arrays take a constant size.
static inline __attribute__((always_inline)) void gru_step(const ns_float_gru_t *g, int in, int hid,
                                                           const float *x, const float *h, float *out)
{
    float gi[3 * hid];
    float gh[3 * hid];
    gate_rows(g->wih, g->bih, 3 * hid, in, x, gi);
    gate_rows(g->whh, g->bhh, 3 * hid, hid, h, gh);
    float rz[2 * hid];
    float n[hid];
    for (int j = 0; j < 2 * hid; j++) {
        rz[j] = sigmoid32(gi[j] + gh[j]);
    }
    for (int j = 0; j < hid; j++) {
        n[j] = tanh32(fmaf(rz[j], gh[2 * hid + j], gi[2 * hid + j]));
    }
    for (int j = 0; j < hid; j++) {
        out[j] = fmaf(rz[hid + j], h[j] - n[j], n[j]);
    }
}

// Four outputs at a time, each summed over the inputs in order.
static inline void dense(const float *w, const float *b, int in, int out_n, const float *x, float *out)
{
    gate_rows(w, b, out_n, in, x, out);
}

static inline void layer_norm(float *x, const float *w, const float *b, int n)
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
    const float inv = rsqrt32(v / (float)n + kNsfLnEps);
    for (int i = 0; i < n; i++) {
        x[i] = fmaf((x[i] - mean) * inv, w[i], b[i]);
    }
}

// out[c][:low] = x[c][:low]; out[c][low + j] = band j's taps over x[c][low:], ascending.
static inline void banded(const float *x, int ch, int fin, int low, const int32_t *ptr, const int32_t *idx,
                          const float *coef, int bands, int fout, float *out)
{
    for (int c = 0; c < ch; c++) {
        memcpy(out + c * fout, x + c * fin, (size_t)low * sizeof(float));
        for (int j = 0; j < bands; j++) {
            float a = 0.0f;
            for (int p = ptr[j]; p < ptr[j + 1]; p++) {
                a = fmaf(coef[p], x[c * fin + low + idx[p]], a);
            }
            out[c * fout + low + j] = a;
        }
    }
}

static inline float mean_square(const float *v, int n)
{
    float a = 0.0f;
    for (int i = 0; i < n; i++) {
        a = fmaf(v[i], v[i], a);
    }
    return a / (float)n;
}

// The dual-path block on x, (NS_FLOAT_CH, NS_FLOAT_F2), in place; inter is its inter GRUs' state, (F2, CH).
static inline void run_dual(const ns_float_dual_t *dp, const ns_float_dual_work_t *wk, float *inter, float *x)
{
    float *t = wk->t;
    float *y = wk->y;
    float *u = wk->u;
    for (int f = 0; f < NS_FLOAT_F2; f++) {
        for (int c = 0; c < NS_FLOAT_CH; c++) {
            t[f * NS_FLOAT_CH + c] = x[c * NS_FLOAT_F2 + f];
        }
    }
    const int quarter = NS_FLOAT_HALF / 2;
    const int rows4 = 3 * quarter;
    float *gi = wk->gi;
    for (int f = 0; f < NS_FLOAT_F2; f++) {
        for (int s = 0; s < 4; s++) {
            const int g = s >> 1;
            const int dir = s & 1;
            gate_rows(dp->intra[g][dir].wih, dp->intra[g][dir].bih, rows4, NS_FLOAT_HALF,
                      t + f * NS_FLOAT_CH + g * NS_FLOAT_HALF, gi + (f * 4 + s) * rows4);
        }
    }
    float h4[4 * NS_FLOAT_HALF / 2] = {0};
    float gh4[4 * 3 * NS_FLOAT_HALF / 2];
    float gi4[4 * 3 * NS_FLOAT_HALF / 2];
    float fresh[4 * NS_FLOAT_HALF / 2];
    for (int step = 0; step < NS_FLOAT_F2; step++) {
        for (int s = 0; s < 4; s++) {
            const int g = s >> 1;
            const int dir = s & 1;
            const int f = dir == 0 ? step : NS_FLOAT_F2 - 1 - step;
            gate_rows(dp->intra[g][dir].whh, dp->intra[g][dir].bhh, rows4, quarter, h4 + s * quarter,
                      gh4 + s * rows4);
            memcpy(gi4 + s * rows4, gi + (f * 4 + s) * rows4, rows4 * sizeof(float));
        }
        gates(4, quarter, gi4, gh4, h4, quarter, wk->rz, wk->nn, fresh, quarter);
        memcpy(h4, fresh, sizeof(h4));
        for (int s = 0; s < 4; s++) {
            const int g = s >> 1;
            const int dir = s & 1;
            const int f = dir == 0 ? step : NS_FLOAT_F2 - 1 - step;
            memcpy(y + f * NS_FLOAT_CH + g * NS_FLOAT_HALF + dir * quarter, h4 + s * quarter,
                   quarter * sizeof(float));
        }
    }
    for (int f = 0; f < NS_FLOAT_F2; f++) {
        dense(dp->intra_fc_w, dp->intra_fc_b, NS_FLOAT_CH, NS_FLOAT_CH, y + f * NS_FLOAT_CH,
              u + f * NS_FLOAT_CH);
    }
    layer_norm(u, dp->intra_ln_w, dp->intra_ln_b, NS_FLOAT_PLANE);
    for (int i = 0; i < NS_FLOAT_PLANE; i++) {
        t[i] = t[i] + u[i];
    }
    const int rows8 = 3 * NS_FLOAT_HALF;
    float *gh = wk->gh;
    for (int f = 0; f < NS_FLOAT_F2; f++) {
        for (int g = 0; g < 2; g++) {
            const int q = f * 2 + g;
            gate_rows(dp->inter[g].wih, dp->inter[g].bih, rows8, NS_FLOAT_HALF,
                      t + f * NS_FLOAT_CH + g * NS_FLOAT_HALF, gi + q * rows8);
            gate_rows(dp->inter[g].whh, dp->inter[g].bhh, rows8, NS_FLOAT_HALF,
                      inter + f * NS_FLOAT_CH + g * NS_FLOAT_HALF, gh + q * rows8);
        }
    }
    gates(2 * NS_FLOAT_F2, NS_FLOAT_HALF, gi, gh, inter, NS_FLOAT_HALF, wk->rz, wk->nn, wk->hn,
          NS_FLOAT_HALF);
    memcpy(inter, wk->hn, NS_FLOAT_PLANE * sizeof(float));
    for (int f = 0; f < NS_FLOAT_F2; f++) {
        dense(dp->inter_fc_w, dp->inter_fc_b, NS_FLOAT_CH, NS_FLOAT_CH, wk->hn + f * NS_FLOAT_CH,
              u + f * NS_FLOAT_CH);
    }
    layer_norm(u, dp->inter_ln_w, dp->inter_ln_b, NS_FLOAT_PLANE);
    for (int f = 0; f < NS_FLOAT_F2; f++) {
        for (int c = 0; c < NS_FLOAT_CH; c++) {
            x[c * NS_FLOAT_F2 + f] = t[f * NS_FLOAT_CH + c] + u[f * NS_FLOAT_CH + c];
        }
    }
}

// A blob of float32 and int32 arrays read in order, 4-byte aligned; ok drops at the first array past the end.
typedef struct {
    const uint8_t *p;
    const uint8_t *end;
    bool ok;
} ns_float_cursor_t;

static inline const void *ns_float_take(ns_float_cursor_t *c, size_t count)
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

static inline ns_float_gru_t ns_float_take_gru(ns_float_cursor_t *c, int in, int hid)
{
    return (ns_float_gru_t){.wih = ns_float_take(c, (size_t)3 * hid * in),
                            .whh = ns_float_take(c, (size_t)3 * hid * hid),
                            .bih = ns_float_take(c, (size_t)3 * hid),
                            .bhh = ns_float_take(c, (size_t)3 * hid)};
}

static inline ns_float_dual_t ns_float_take_dual(ns_float_cursor_t *c)
{
    ns_float_dual_t d;
    for (int g = 0; g < 2; g++) {
        d.intra[g][0] = ns_float_take_gru(c, NS_FLOAT_HALF, NS_FLOAT_HALF / 2);
        d.intra[g][1] = ns_float_take_gru(c, NS_FLOAT_HALF, NS_FLOAT_HALF / 2);
    }
    d.intra_fc_w = ns_float_take(c, NS_FLOAT_CH * NS_FLOAT_CH);
    d.intra_fc_b = ns_float_take(c, NS_FLOAT_CH);
    d.intra_ln_w = ns_float_take(c, NS_FLOAT_PLANE);
    d.intra_ln_b = ns_float_take(c, NS_FLOAT_PLANE);
    for (int g = 0; g < 2; g++) {
        d.inter[g] = ns_float_take_gru(c, NS_FLOAT_HALF, NS_FLOAT_HALF);
    }
    d.inter_fc_w = ns_float_take(c, NS_FLOAT_CH * NS_FLOAT_CH);
    d.inter_fc_b = ns_float_take(c, NS_FLOAT_CH);
    d.inter_ln_w = ns_float_take(c, NS_FLOAT_PLANE);
    d.inter_ln_b = ns_float_take(c, NS_FLOAT_PLANE);
    return d;
}
