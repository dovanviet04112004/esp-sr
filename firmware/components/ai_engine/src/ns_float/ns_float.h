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
static const float kNsfActLimit = 87.0f;
static const float kNsfRoundShift = 0x1.8p23f;
static const float kNsfLnEps = 0x1.5798eep-27f;
static const float kNsfRecipC0 = 0x1.69696ap+1f;
static const float kNsfRecipC1 = 0x1.e1e1e2p+0f;
static const float kNsfRsqrtLow0 = 0x1.d413ccp+0f;
static const float kNsfRsqrtLow1 = 0x1.a82798p-1f;
static const float kNsfRsqrtHigh0 = 0x1.4afb0cp+0f;
static const float kNsfRsqrtHigh1 = 0x1.2bec32p-2f;
static const float kNsfTinySquare = 0x1p-100f;
static const float kNsfExpPoly[] = {0x1.0fe5bep-7f, 0x1.575eeep-5f, 0x1.555a18p-3f,
                                    0x1.fffd1ap-2f, 0x1.fffff6p-1f, 0x1.000002p+0f};
static const float kNsfSeed[] = {0x1.0f836ap+1f, -0x1.745c28p+0f, 0x1.4afc94p-2f};

// Every small numeric step below is inlined: called at each gate, a call costs as much as the step.

static inline __attribute__((always_inline)) float pow2i(int n)
{
    const union {
        uint32_t u;
        float f;
    } v = {.u = (uint32_t)(127 + n) << 23};
    return v.f;
}

// e^-a for 0 <= a <= kNsfActLimit, as gtcrn_mirror.exp_neg32 takes it.
static inline __attribute__((always_inline)) float exp_neg32(float a)
{
    const float n = (a * kNsfLog2e + kNsfRoundShift) - kNsfRoundShift;
    const float s = -fmaf(-n, kNsfLn2Lo, fmaf(-n, kNsfLn2Hi, a));
    float p = kNsfExpPoly[0];
#pragma GCC unroll 8
    for (size_t j = 1; j < sizeof(kNsfExpPoly) / sizeof(kNsfExpPoly[0]); j++) {
        p = fmaf(p, s, kNsfExpPoly[j]);
    }
    return p * pow2i(-(int)n);
}

// 1 / d for d in (1, 2], as gtcrn_mirror.recip12 takes it.
static inline __attribute__((always_inline)) float recip12(float d)
{
    float y = fmaf(fmaf(kNsfSeed[2], d, kNsfSeed[1]), d, kNsfSeed[0]);
#pragma GCC unroll 4
    for (int i = 0; i < NS_FLOAT_SEED_STEPS; i++) {
        y = fmaf(y, fmaf(-d, y, 1.0f), y);
    }
    return y;
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
#pragma GCC unroll 4
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
#pragma GCC unroll 4
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
    const float a = fabsf(v);
    const float u = exp_neg32(a < kNsfActLimit ? a : kNsfActLimit);
    const float y = recip12(1.0f + u);
    return v >= 0.0f ? y : u * y;
}

static inline __attribute__((always_inline)) float tanh32(float v)
{
    const float a = fabsf(v) * 2.0f;
    const float u = exp_neg32(a < kNsfActLimit ? a : kNsfActLimit);
    const float t = (1.0f - u) * recip12(1.0f + u);
    return v < 0.0f ? -t : t;
}

// sigmoid32 or tanh32 on four lanes, step by step: GCC keeps this order, and only interleaved lanes hide the
// FPU's 4-cycle latency (afe/ns.md 10.8). Each lane runs the scalar steps unchanged.
#define NSF_LANES(step) step(0) step(1) step(2) step(3)
#define NSF_ARG(k)                                                                                           \
    float a##k = tanh_form ? fabsf(v[k]) * 2.0f : fabsf(v[k]);                                               \
    a##k = a##k < kNsfActLimit ? a##k : kNsfActLimit;
#define NSF_ROUND(k) const float n##k = (a##k * kNsfLog2e + kNsfRoundShift) - kNsfRoundShift;
#define NSF_REDUCE(k) const float s##k = -fmaf(-n##k, kNsfLn2Lo, fmaf(-n##k, kNsfLn2Hi, a##k));
#define NSF_POLY_START(k) float p##k = kNsfExpPoly[0];
#define NSF_POLY(k) p##k = fmaf(p##k, s##k, kNsfExpPoly[j]);
#define NSF_SCALE(k) const float u##k = p##k * pow2i(-(int)n##k);
#define NSF_SEED(k) float y##k = fmaf(fmaf(kNsfSeed[2], 1.0f + u##k, kNsfSeed[1]), 1.0f + u##k, kNsfSeed[0]);
#define NSF_NEWTON(k) y##k = fmaf(y##k, fmaf(-(1.0f + u##k), y##k, 1.0f), y##k);
#define NSF_OUT(k)                                                                                           \
    v[k] = tanh_form ? (v[k] < 0.0f ? -((1.0f - u##k) * y##k) : (1.0f - u##k) * y##k)                        \
                     : (v[k] >= 0.0f ? y##k : u##k * y##k);

static inline __attribute__((always_inline)) void act4(float *v, bool tanh_form)
{
    NSF_LANES(NSF_ARG);
    NSF_LANES(NSF_ROUND);
    NSF_LANES(NSF_REDUCE);
    NSF_LANES(NSF_POLY_START);
#pragma GCC unroll 8
    for (size_t j = 1; j < sizeof(kNsfExpPoly) / sizeof(kNsfExpPoly[0]); j++) {
        NSF_LANES(NSF_POLY);
    }
    NSF_LANES(NSF_SCALE);
    NSF_LANES(NSF_SEED);
#pragma GCC unroll 4
    for (int step = 0; step < NS_FLOAT_SEED_STEPS; step++) {
        NSF_LANES(NSF_NEWTON);
    }
    NSF_LANES(NSF_OUT);
}

static inline __attribute__((always_inline)) void sigmoid4(float *v)
{
    act4(v, false);
}

static inline __attribute__((always_inline)) void tanh4(float *v)
{
    act4(v, true);
}

// v[i] = sigmoid32(v[i]) for n values, bit for bit.
static inline void sigmoid_n(float *v, int n)
{
    int i = 0;
    for (; i + 4 <= n; i += 4) {
        sigmoid4(v + i);
    }
    for (; i < n; i++) {
        v[i] = sigmoid32(v[i]);
    }
}

// v[i] = tanh32(v[i]) for n values, bit for bit.
static inline void tanh_n(float *v, int n)
{
    int i = 0;
    for (; i + 4 <= n; i += 4) {
        tanh4(v + i);
    }
    for (; i < n; i++) {
        v[i] = tanh32(v[i]);
    }
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
            rz[q * 2 * hid + j] = gi[q * 3 * hid + j] + gh[q * 3 * hid + j];
        }
    }
    sigmoid_n(rz, count * 2 * hid);
    for (int q = 0; q < count; q++) {
        for (int j = 0; j < hid; j++) {
            const int at = q * 3 * hid + 2 * hid + j;
            n[q * hid + j] = fmaf(rz[q * 2 * hid + j], gh[at], gi[at]);
        }
    }
    tanh_n(n, count * hid);
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
        rz[j] = gi[j] + gh[j];
    }
    sigmoid_n(rz, 2 * hid);
    for (int j = 0; j < hid; j++) {
        n[j] = fmaf(rz[j], gh[2 * hid + j], gi[2 * hid + j]);
    }
    tanh_n(n, hid);
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
