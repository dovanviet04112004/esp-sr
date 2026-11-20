#include <stddef.h>

#include "espdl_chip_kernels.h"
#include "espdl_kernels.hpp"

enum { kOutputs = 16, kBiasBytes = 64, kHalf = 8 };

static int32_t dot_from(int32_t acc, const int8_t *x, const int8_t *block, int k, int n)
{
    for (int i = 0; i < k; i++) {
        acc = chip_mac(acc, x[i], block[i * kOutputs + n]);
    }
    return acc;
}

static int run_inputs(const int32_t *run)
{
    return run[3] * kOutputs + (run[4] == 0 ? kOutputs : kHalf);
}

// The convolution, ReLU, then the norm's depthwise 1x1 with its bias: 16 outputs a block.
static void norm_rows(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run, int k,
                      int stride, int blocks)
{
    const int less = run[5] - 1;
    const int8_t *bias = AI_ENGINE_RUN_PTR(const int8_t *, run, 6);
    const int8_t *norm = AI_ENGINE_RUN_PTR(const int8_t *, run, 7);
    const int8_t *norm_bias = AI_ENGINE_RUN_PTR(const int8_t *, run, 8);
    const int norm_less = run[9] - 1;
    for (int p = 0; p < run[0]; p++) {
        const int8_t *x = in + p * stride;
        for (int b = 0; b < blocks; b++) {
            for (int n = 0; n < kOutputs; n++) {
                const int32_t acc =
                    dot_from(chip_bias(bias + b * kBiasBytes, n), x, filter + b * k * kOutputs, k, n);
                int8_t v = chip_round(acc, less);
                v = v < 0 ? 0 : v;
                const int32_t scaled =
                    chip_mac(chip_bias(norm_bias + b * kBiasBytes, n), v, norm[b * kOutputs + n]);
                *out++ = chip_round(scaled, norm_less);
            }
        }
    }
}

// The convolution plus x (as it is when x_less < 0, else rounded right first), then ReLU; the last block
// stores last.
static void residual_rows(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run, int k,
                          int stride, int blocks, int x_less, int last)
{
    const int less = run[5] - 1;
    const int8_t *bias = AI_ENGINE_RUN_PTR(const int8_t *, run, 6);
    const int8_t *x_in = AI_ENGINE_RUN_PTR(const int8_t *, run, 7);
    for (int p = 0; p < run[0]; p++) {
        const int8_t *x = in + p * stride;
        for (int b = 0; b < blocks; b++, x_in += kOutputs) {
            const int width = b == blocks - 1 ? last : kOutputs;
            for (int n = 0; n < width; n++) {
                const int32_t acc =
                    dot_from(chip_bias(bias + b * kBiasBytes, n), x, filter + b * k * kOutputs, k, n);
                const int8_t added = x_less < 0 ? x_in[n] : chip_round(x_in[n], x_less);
                const int8_t v = chip_adds8(chip_round(acc, less), added);
                *out++ = v < 0 ? 0 : v;
            }
        }
    }
}

void ai_engine_pw_norm_row_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
{
    norm_rows(out, in, filter, run, run_inputs(run), run[2] * kOutputs, run[1]);
}

void ai_engine_pw_residual_row_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
{
    const int last = run[9] == kHalf ? kHalf : kOutputs;
    residual_rows(out, in, filter, run, run_inputs(run), run[2] * kOutputs, run[1], run[8] - 1, last);
}

void ai_engine_pw_bias_row_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
{
    const int blocks = run[1];
    const int k = run_inputs(run);
    const int less = run[5] - 1;
    const int8_t *bias = AI_ENGINE_RUN_PTR(const int8_t *, run, 6);
    const int relu = run[7];
    for (int r = 0; r < run[0]; r++) {
        const int8_t *x = in + r * run[2] * kOutputs;
        for (int b = 0; b < blocks; b++) {
            const int width = b == blocks - 1 && run[8] == kHalf ? kHalf : kOutputs;
            for (int n = 0; n < width; n++) {
                int8_t v = chip_round(dot_from(0, x, filter + b * k * kOutputs, k, n), less);
                if (relu && v < 0) { v = 0; }
                if (bias != NULL) { v = chip_adds8(v, bias[b * kOutputs + n]); }
                *out++ = v;
            }
        }
    }
}

#define UNROLLED(c)                                                                                          \
    void ai_engine_pw_norm_c##c##_s8(int8_t *out, const int8_t *in, const int8_t *filter,                    \
                                     const int32_t *run)                                                     \
    {                                                                                                        \
        norm_rows(out, in, filter, run, c, run[2] * kOutputs, run[1]);                                       \
    }                                                                                                        \
    void ai_engine_pw_residual_c##c##_s8(int8_t *out, const int8_t *in, const int8_t *filter,                \
                                         const int32_t *run)                                                 \
    {                                                                                                        \
        residual_rows(out, in, filter, run, c, run[2] * kOutputs, run[1], -1, kOutputs);                     \
    }                                                                                                        \
    void ai_engine_pw_residual_x_c##c##_s8(int8_t *out, const int8_t *in, const int8_t *filter,              \
                                           const int32_t *run)                                               \
    {                                                                                                        \
        residual_rows(out, in, filter, run, c, run[2] * kOutputs, run[1], run[8] - 1, kOutputs);             \
    }

// A pixel of c channels held whole: ceil(c / 16) blocks, inputs ceil(c / 16) vectors apart, c outputs stored.
#define FIXED(c)                                                                                             \
    void ai_engine_pw_norm_c##c##_s8(int8_t *out, const int8_t *in, const int8_t *filter,                    \
                                     const int32_t *run)                                                     \
    {                                                                                                        \
        norm_rows(out, in, filter, run, c, (c + 15) / 16 * kOutputs, (c + 15) / 16);                         \
    }                                                                                                        \
    void ai_engine_pw_residual_c##c##_s8(int8_t *out, const int8_t *in, const int8_t *filter,                \
                                         const int32_t *run)                                                 \
    {                                                                                                        \
        residual_rows(out, in, filter, run, c, (c + 15) / 16 * kOutputs, (c + 15) / 16, -1,                  \
                      c % kOutputs ? c % kOutputs : kOutputs);                                               \
    }                                                                                                        \
    void ai_engine_pw_residual_x_c##c##_s8(int8_t *out, const int8_t *in, const int8_t *filter,              \
                                           const int32_t *run)                                               \
    {                                                                                                        \
        residual_rows(out, in, filter, run, c, (c + 15) / 16 * kOutputs, (c + 15) / 16, run[8] - 1,          \
                      c % kOutputs ? c % kOutputs : kOutputs);                                               \
    }

FIXED(24)
FIXED(48)
UNROLLED(96)
UNROLLED(144)
UNROLLED(192)
