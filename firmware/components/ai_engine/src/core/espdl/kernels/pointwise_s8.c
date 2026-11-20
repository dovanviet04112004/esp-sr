#include <stddef.h>

#include "espdl_chip_kernels.h"
#include "espdl_kernels.hpp"

enum { kOutputs = 16, kBiasBytes = 64, kHalf = 8 };

// One output of a block from acc on: the block's weights are [K][16].
static int32_t dot_from(int32_t acc, const int8_t *x, const int8_t *block, int k, int n)
{
    for (int i = 0; i < k; i++) {
        acc = chip_mac(acc, x[i], block[i * kOutputs + n]);
    }
    return acc;
}

// residual 0 none, 1 added, 2 rounded right first as RequantizeLinear does; k 0 takes K from the run.
static void pw_rows(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run, int k,
                    int residual)
{
    const int rows = run[0];
    const int blocks = run[1];
    const int stride = run[2] * kOutputs;
    if (k == 0) { k = run[3] * kOutputs + (run[4] == 0 ? kOutputs : kHalf); }
    const int less = run[5] - 1;
    const int8_t *bias = AI_ENGINE_RUN_PTR(const int8_t *, run, 6);
    const int8_t *res = residual ? AI_ENGINE_RUN_PTR(const int8_t *, run, 7) : NULL;
    const int res_less = run[8] - 1;
    for (int r = 0; r < rows; r++) {
        const int8_t *x = in + r * stride;
        for (int b = 0; b < blocks; b++) {
            const int width = b == blocks - 1 && run[9] == kHalf ? kHalf : kOutputs;
            for (int n = 0; n < width; n++) {
                const int32_t acc =
                    dot_from(chip_bias(bias + b * kBiasBytes, n), x, filter + b * k * kOutputs, k, n);
                int8_t v = chip_round(acc, less);
                if (res != NULL) { v = chip_adds8(v, residual == 2 ? chip_round(res[n], res_less) : res[n]); }
                out[n] = v;
            }
            out += width;
            if (res != NULL) { res += width; }
        }
        out += run[10];
        if (res != NULL) { res += run[10]; }
    }
}

// Rows of k inputs back to back; the last block stores run[9] outputs, or 12 with twelve.
static void held_rows(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run, int k,
                      int residual, int twelve)
{
    const int rows = run[0];
    const int blocks = run[1];
    const int less = run[5] - 1;
    const int8_t *bias = AI_ENGINE_RUN_PTR(const int8_t *, run, 6);
    const int8_t *res = residual ? AI_ENGINE_RUN_PTR(const int8_t *, run, 7) : NULL;
    const int res_less = run[8] - 1;
    for (int r = 0; r < rows; r++) {
        const int8_t *x = in + r * k;
        for (int b = 0; b < blocks; b++) {
            const int width = b < blocks - 1 ? kOutputs : twelve ? 12 : run[9];
            for (int n = 0; n < width; n++) {
                const int32_t acc =
                    dot_from(chip_bias(bias + b * kBiasBytes, n), x, filter + b * k * kOutputs, k, n);
                int8_t v = chip_round(acc, less);
                if (res != NULL) { v = chip_adds8(v, residual == 2 ? chip_round(res[n], res_less) : res[n]); }
                out[n] = v;
            }
            out += width;
            if (res != NULL) { res += width; }
        }
    }
}

void ai_engine_pw_conv_rows_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
{
    pw_rows(out, in, filter, run, 0, 0);
}

void ai_engine_pw_add_rows_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
{
    pw_rows(out, in, filter, run, 0, 1);
}

void ai_engine_pw_add_x_rows_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
{
    pw_rows(out, in, filter, run, 0, 2);
}

#define HELD(k)                                                                                              \
    void ai_engine_pw_conv_k##k##_s8(int8_t *out, const int8_t *in, const int8_t *filter,                    \
                                     const int32_t *run)                                                     \
    {                                                                                                        \
        held_rows(out, in, filter, run, k, 0, 0);                                                            \
    }                                                                                                        \
    void ai_engine_pw_add_k##k##_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run) \
    {                                                                                                        \
        held_rows(out, in, filter, run, k, 1, 0);                                                            \
    }                                                                                                        \
    void ai_engine_pw_add_x_k##k##_s8(int8_t *out, const int8_t *in, const int8_t *filter,                   \
                                      const int32_t *run)                                                    \
    {                                                                                                        \
        held_rows(out, in, filter, run, k, 2, 0);                                                            \
    }

#define UNROLLED(k)                                                                                          \
    void ai_engine_pw_conv_k##k##_s8(int8_t *out, const int8_t *in, const int8_t *filter,                    \
                                     const int32_t *run)                                                     \
    {                                                                                                        \
        pw_rows(out, in, filter, run, k, 0);                                                                 \
    }                                                                                                        \
    void ai_engine_pw_add_k##k##_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run) \
    {                                                                                                        \
        pw_rows(out, in, filter, run, k, 1);                                                                 \
    }                                                                                                        \
    void ai_engine_pw_add_x_k##k##_s8(int8_t *out, const int8_t *in, const int8_t *filter,                   \
                                      const int32_t *run)                                                    \
    {                                                                                                        \
        pw_rows(out, in, filter, run, k, 2);                                                                 \
    }

HELD(24)
HELD(48)
UNROLLED(72)
UNROLLED(96)
UNROLLED(128)
UNROLLED(144)
UNROLLED(192)

void ai_engine_pw_conv_k12_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
{
    held_rows(out, in, filter, run, 12, 0, 0);
}

void ai_engine_pw_conv_k24_n12_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
{
    held_rows(out, in, filter, run, 24, 0, 1);
}
