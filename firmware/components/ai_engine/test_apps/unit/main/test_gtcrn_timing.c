#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "unity.h"

#define BINS 257
#define LOW 65
#define ERB 129
#define F1 65
#define F2 33
#define CH 16
#define HALF 8
#define HOPS 200
#define WARM 20

typedef struct {
    int cin, cout, kt, kf, stride, pad, dil, groups, fin, fout;
    float *w, *b, *slope;
} conv_t;

typedef struct {
    int in, hid;
    float *wih, *whh, *bih, *bhh;
} gru_t;

typedef struct {
    conv_t pc1, depth, pc2;
    gru_t att;
    float *att_w, *att_b, *cache;
    int frames;
    float tra[2 * HALF];
} block_t;

typedef struct {
    gru_t intra[2][2], inter[2];
    float *fc1_w, *fc1_b, *ln1_w, *ln1_b, *fc2_w, *fc2_b, *ln2_w, *ln2_b;
    float h[F2 * CH];
} dp_t;

static uint32_t s_seed = 0x1234567u;
static int s_fast;
static float s_pin[8192], s_acc[ERB];
static uint32_t s_caps;
static size_t s_floats;

static float *weights(size_t n)
{
    float *p = heap_caps_malloc(n * sizeof(float), s_caps);
    TEST_ASSERT_NOT_NULL(p);
    for (size_t i = 0; i < n; i++) {
        s_seed ^= s_seed << 13;
        s_seed ^= s_seed >> 17;
        s_seed ^= s_seed << 5;
        p[i] = ((float)(s_seed & 0xffff) / 65536.0f - 0.5f) * 0.2f;
    }
    s_floats += n;
    return p;
}

static conv_t conv(int cin, int cout, int kt, int kf, int stride, int pad, int dil, int groups, int fin)
{
    conv_t c = {.cin = cin,
                .cout = cout,
                .kt = kt,
                .kf = kf,
                .stride = stride,
                .pad = pad,
                .dil = dil,
                .groups = groups,
                .fin = fin,
                .fout = (fin + 2 * pad - kf) / stride + 1};
    c.w = weights((size_t)cout * (cin / groups) * kt * kf);
    c.b = weights(cout);
    c.slope = weights(cout);
    return c;
}

static gru_t gru(int in, int hid)
{
    gru_t g = {.in = in, .hid = hid};
    g.wih = weights(3 * hid * in), g.whh = weights(3 * hid * hid), g.bih = weights(3 * hid),
    g.bhh = weights(3 * hid);
    return g;
}

static void conv_fast(const conv_t *c, const float *in, float *out, int act);

// in: [cin][(kt - 1) * dil + 1][fin], the cache's frames then this hop's; out: [cout][fout].
static void conv_run(const conv_t *c, const float *in, float *out, int act)
{
    if (s_fast) {
        conv_fast(c, in, out, act);
        return;
    }
    const int per_in = c->cin / c->groups, per_out = c->cout / c->groups, span = (c->kt - 1) * c->dil + 1;
    for (int co = 0; co < c->cout; co++) {
        const int g = co / per_out;
        const float *w = c->w + (size_t)co * per_in * c->kt * c->kf;
        for (int fo = 0; fo < c->fout; fo++) {
            float acc = c->b[co];
            for (int ci = 0; ci < per_in; ci++) {
                const float *x = in + (size_t)(g * per_in + ci) * span * c->fin;
                for (int t = 0; t < c->kt; t++) {
                    const float *row = x + (size_t)t * c->dil * c->fin;
                    const float *wk = w + (ci * c->kt + t) * c->kf;
                    for (int k = 0; k < c->kf; k++) {
                        const int f = fo * c->stride - c->pad + k;
                        if (f >= 0 && f < c->fin) { acc += wk[k] * row[f]; }
                    }
                }
            }
            out[co * c->fout + fo] = act == 1   ? (acc > 0.0f ? acc : c->slope[co] * acc)
                                     : act == 2 ? tanhf(acc)
                                                : acc;
        }
    }
}

static float fast_exp(float x)
{
    x = x < -80.0f ? -80.0f : x > 80.0f ? 80.0f : x;
    const float t = x * 1.442695041f, n = floorf(t), f = t - n;
    union {
        float f;
        int32_t i;
    } u = {.f = 1.0f + f * (0.6931472f + f * (0.2402265f + f * (0.0555041f + f * 0.0096181f)))};
    u.i += (int32_t)n << 23;
    return u.f;
}

static float sigmoid(float v)
{
    return 1.0f / (1.0f + (s_fast ? fast_exp(-v) : expf(-v)));
}

static float tanh_of(float v)
{
    return s_fast ? 2.0f * sigmoid(2.0f * v) - 1.0f : tanhf(v);
}

// Each input row padded once, then a weight at a time over every output position: no bounds test inside.
static void conv_fast(const conv_t *c, const float *in, float *out, int act)
{
    const int per_in = c->cin / c->groups, per_out = c->cout / c->groups, span = (c->kt - 1) * c->dil + 1;
    const int width = c->fin + 2 * c->pad + c->stride;
    for (int r = 0; r < c->cin * span; r++) {
        float *dst = s_pin + r * width;
        memset(dst, 0, (size_t)width * sizeof(float));
        memcpy(dst + c->pad, in + (size_t)r * c->fin, (size_t)c->fin * sizeof(float));
    }
    for (int co = 0; co < c->cout; co++) {
        const int g = co / per_out;
        const float *w = c->w + (size_t)co * per_in * c->kt * c->kf;
        for (int fo = 0; fo < c->fout; fo++) {
            s_acc[fo] = c->b[co];
        }
        for (int ci = 0; ci < per_in; ci++) {
            for (int t = 0; t < c->kt; t++) {
                const float *row = s_pin + ((g * per_in + ci) * span + t * c->dil) * width;
                const float *wk = w + (ci * c->kt + t) * c->kf;
                for (int k = 0; k < c->kf; k++) {
                    const float wv = wk[k];
                    const float *x = row + k;
                    if (c->stride == 1) {
                        for (int fo = 0; fo < c->fout; fo++) {
                            s_acc[fo] += wv * x[fo];
                        }
                    } else {
                        for (int fo = 0; fo < c->fout; fo++) {
                            s_acc[fo] += wv * x[2 * fo];
                        }
                    }
                }
            }
        }
        for (int fo = 0; fo < c->fout; fo++) {
            const float a = s_acc[fo];
            out[co * c->fout + fo] = act == 1 ? (a > 0.0f ? a : c->slope[co] * a) : act == 2 ? tanh_of(a) : a;
        }
    }
}

static void gru_step(const gru_t *g, const float *x, float *h)
{
    float gi[48], gh[48];
    for (int r = 0; r < 3 * g->hid; r++) {
        float a = g->bih[r], b = g->bhh[r];
        for (int i = 0; i < g->in; i++) {
            a += g->wih[r * g->in + i] * x[i];
        }
        for (int i = 0; i < g->hid; i++) {
            b += g->whh[r * g->hid + i] * h[i];
        }
        gi[r] = a;
        gh[r] = b;
    }
    for (int i = 0; i < g->hid; i++) {
        const float r = sigmoid(gi[i] + gh[i]), z = sigmoid(gi[g->hid + i] + gh[g->hid + i]);
        const float n = tanh_of(gi[2 * g->hid + i] + r * gh[2 * g->hid + i]);
        h[i] = (1.0f - z) * n + z * h[i];
    }
}

static block_t block(int dil)
{
    block_t b = {0};
    b.frames = 2 * dil;
    b.pc1 = conv(3 * HALF, CH, 1, 1, 1, 0, 1, 1, F2);
    b.depth = conv(CH, CH, 3, 3, 1, 1, dil, CH, F2);
    b.pc2 = conv(CH, HALF, 1, 1, 1, 0, 1, 1, F2);
    b.att = gru(HALF, 2 * HALF);
    b.att_w = weights(HALF * 2 * HALF);
    b.att_b = weights(HALF);
    b.cache = heap_caps_calloc((size_t)CH * (b.frames + 1) * F2, sizeof(float), s_caps);
    TEST_ASSERT_NOT_NULL(b.cache);
    return b;
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

static void block_run(block_t *b, float *x)
{
    static float s[3 * HALF * F2], h[CH * F2], y[HALF * F2], z[HALF], at[HALF], out[CH * F2];
    sfe(x, HALF, F2, s);
    conv_run(&b->pc1, s, h, 1);
    const int span = b->frames + 1;
    for (int c = 0; c < CH; c++) {
        memmove(b->cache + c * span * F2, b->cache + c * span * F2 + F2,
                (size_t)b->frames * F2 * sizeof(float));
        memcpy(b->cache + (c * span + b->frames) * F2, h + c * F2, F2 * sizeof(float));
    }
    conv_run(&b->depth, b->cache, h, 1);
    conv_run(&b->pc2, h, y, 0);
    for (int c = 0; c < HALF; c++) {
        float e = 0.0f;
        for (int i = 0; i < F2; i++) {
            e += y[c * F2 + i] * y[c * F2 + i];
        }
        z[c] = e / F2;
    }
    gru_step(&b->att, z, b->tra);
    for (int c = 0; c < HALF; c++) {
        float a = b->att_b[c];
        for (int i = 0; i < 2 * HALF; i++) {
            a += b->att_w[c * 2 * HALF + i] * b->tra[i];
        }
        at[c] = sigmoid(a);
    }
    for (int c = 0; c < HALF; c++) {
        for (int i = 0; i < F2; i++) {
            out[(2 * c) * F2 + i] = y[c * F2 + i] * at[c];
            out[(2 * c + 1) * F2 + i] = x[(HALF + c) * F2 + i];
        }
    }
    memcpy(x, out, sizeof(out));
}

static void dense(const float *w, const float *b, const float *x, float *y, int rows, int in, int out)
{
    for (int r = 0; r < rows; r++) {
        for (int o = 0; o < out; o++) {
            float a = b[o];
            for (int i = 0; i < in; i++) {
                a += w[o * in + i] * x[r * in + i];
            }
            y[r * out + o] = a;
        }
    }
}

static void layer_norm(float *x, const float *w, const float *b, int n)
{
    float mean = 0.0f, var = 0.0f;
    for (int i = 0; i < n; i++) {
        mean += x[i];
    }
    mean /= n;
    for (int i = 0; i < n; i++) {
        var += (x[i] - mean) * (x[i] - mean);
    }
    const float inv = 1.0f / sqrtf(var / n + 1e-8f);
    for (int i = 0; i < n; i++) {
        x[i] = (x[i] - mean) * inv * w[i] + b[i];
    }
}

static dp_t dp(void)
{
    dp_t d = {0};
    for (int g = 0; g < 2; g++) {
        d.intra[g][0] = gru(HALF, HALF / 2);
        d.intra[g][1] = gru(HALF, HALF / 2);
        d.inter[g] = gru(HALF, HALF);
    }
    d.fc1_w = weights(CH * CH), d.fc1_b = weights(CH), d.ln1_w = weights(F2 * CH), d.ln1_b = weights(F2 * CH);
    d.fc2_w = weights(CH * CH), d.fc2_b = weights(CH), d.ln2_w = weights(F2 * CH), d.ln2_b = weights(F2 * CH);
    return d;
}

static void dp_run(dp_t *d, float *x)
{
    static float t[F2 * CH], y[F2 * CH], u[F2 * CH], hf[HALF / 2], hb[HALF / 2];
    for (int f = 0; f < F2; f++) {
        for (int c = 0; c < CH; c++) {
            t[f * CH + c] = x[c * F2 + f];
        }
    }
    for (int g = 0; g < 2; g++) {
        memset(hf, 0, sizeof(hf));
        memset(hb, 0, sizeof(hb));
        for (int f = 0; f < F2; f++) {
            gru_step(&d->intra[g][0], t + f * CH + g * HALF, hf);
            memcpy(y + f * CH + g * HALF, hf, sizeof(hf));
            gru_step(&d->intra[g][1], t + (F2 - 1 - f) * CH + g * HALF, hb);
            memcpy(y + (F2 - 1 - f) * CH + g * HALF + HALF / 2, hb, sizeof(hb));
        }
    }
    dense(d->fc1_w, d->fc1_b, y, u, F2, CH, CH);
    layer_norm(u, d->ln1_w, d->ln1_b, F2 * CH);
    for (int i = 0; i < F2 * CH; i++) {
        t[i] += u[i];
    }
    for (int f = 0; f < F2; f++) {
        for (int g = 0; g < 2; g++) {
            gru_step(&d->inter[g], t + f * CH + g * HALF, d->h + f * CH + g * HALF);
        }
    }
    dense(d->fc2_w, d->fc2_b, d->h, u, F2, CH, CH);
    layer_norm(u, d->ln2_w, d->ln2_b, F2 * CH);
    for (int f = 0; f < F2; f++) {
        for (int c = 0; c < CH; c++) {
            x[c * F2 + f] = t[f * CH + c] + u[f * CH + c];
        }
    }
}

static void upsample(const float *x, int ch, int f, float *out)
{
    for (int c = 0; c < ch; c++) {
        for (int i = 0; i < 2 * f - 1; i++) {
            out[c * (2 * f - 1) + i] = i % 2 ? 0.0f : x[c * f + i / 2];
        }
    }
}

static void erb(const float *x, float *out, int ch, int from, int to, int taps)
{
    for (int c = 0; c < ch; c++) {
        memcpy(out + c * to, x + c * from, LOW * sizeof(float));
        for (int band = LOW; band < to; band++) {
            float a = 0.0f;
            for (int k = 0; k < taps; k++) {
                a += 0.5f * x[c * from + LOW + ((band - LOW) * (from - LOW) / (to - LOW) + k) % (from - LOW)];
            }
            out[c * to + band] = a;
        }
    }
}

static void run(uint32_t caps, const char *where, int fast)
{
    s_caps = caps;
    s_fast = fast;
    s_floats = 0;
    conv_t en0 = conv(9, CH, 1, 5, 2, 2, 1, 1, ERB), en1 = conv(CH, CH, 1, 5, 2, 2, 1, 2, F1);
    static block_t enc[3], dec[3];
    static dp_t dps[2];
    const int dils[3] = {1, 2, 5};
    for (int k = 0; k < 3; k++) {
        enc[k] = block(dils[k]);
        dec[k] = block(dils[2 - k]);
    }
    dps[0] = dp();
    dps[1] = dp();
    conv_t de3 = conv(CH, CH, 1, 5, 1, 2, 1, 2, 2 * F2 - 1), de4 = conv(CH, 2, 1, 5, 1, 2, 1, 1, 2 * F1 - 1);
    static float x[3 * BINS], e[3 * ERB], s[9 * ERB], o0[CH * F1], o1[CH * F2], skip[3][CH * F2], y[CH * F2];
    static float up1[CH * F1], m1[CH * F1], up2[CH * ERB], m2[2 * ERB], mask[2 * BINS];
    for (int i = 0; i < 3 * BINS; i++) {
        x[i] = (float)((i * 37) % 101) / 101.0f - 0.5f;
    }
    int64_t total = 0, worst = 0;
    for (int hop = 0; hop < WARM + HOPS; hop++) {
        const int64_t t0 = esp_timer_get_time();
        erb(x, e, 3, BINS, ERB, 3);
        sfe(e, 3, ERB, s);
        conv_run(&en0, s, o0, 1);
        conv_run(&en1, o0, o1, 1);
        memcpy(y, o1, sizeof(y));
        for (int k = 0; k < 3; k++) {
            block_run(&enc[k], y);
            memcpy(skip[k], y, sizeof(y));
        }
        dp_run(&dps[0], y);
        dp_run(&dps[1], y);
        for (int k = 0; k < 3; k++) {
            for (int i = 0; i < CH * F2; i++) {
                y[i] += skip[2 - k][i];
            }
            block_run(&dec[k], y);
        }
        for (int i = 0; i < CH * F2; i++) {
            y[i] += o1[i];
        }
        upsample(y, CH, F2, up1);
        conv_run(&de3, up1, m1, 1);
        for (int i = 0; i < CH * F1; i++) {
            m1[i] += o0[i];
        }
        upsample(m1, CH, F1, up2);
        conv_run(&de4, up2, m2, 2);
        erb(m2, mask, 2, ERB, BINS, 2);
        const int64_t dt = esp_timer_get_time() - t0;
        if (hop >= WARM) {
            total += dt;
            worst = dt > worst ? dt : worst;
        }
        vTaskDelay(1);
    }
    printf("gtcrn f32 timing, %s loops, weights in %s: %u floats, mean %lld us, worst %lld us a hop, mask[0] "
           "%f\n",
           fast ? "padded" : "plain", where, (unsigned)s_floats, (long long)(total / HOPS), (long long)worst,
           (double)mask[0]);
}

TEST_CASE("gtcrn float32 hop shaped as the net, random weights", "[gtcrn]")
{
    run(MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT, "PSRAM", 1);
    run(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT, "internal RAM", 1);
    run(MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT, "PSRAM", 0);
}

void app_main(void)
{
    UNITY_BEGIN();
    unity_run_tests_by_tag("[gtcrn]", false);
    UNITY_END();
}
