#include <stddef.h>
#include <string.h>

#include "espdl_chip_kernels.h"
#include "espdl_kernels.hpp"

enum { kVector = 16, kBiasBytes = 64, kHalf = 8, kTaps3x3 = 9 };

static int32_t bias_or_zero(const int8_t *bias, int v, int c)
{
    return bias != NULL ? chip_bias(bias + v * kBiasBytes, c) : 0;
}

static void dw3x3(int8_t *out, const int8_t *const *rows, const int8_t *taps, const int32_t *run,
                  const int8_t *bias)
{
    const int pixels = run[0];
    const int width = run[1] * kVector;
    const int less = run[2] - 1;
    for (int f = 0; f < pixels; f++) {
        for (int i = 0; i < width; i++) {
            int32_t acc = bias_or_zero(bias, i / kVector, i % kVector);
            for (int kh = 0; kh < 3; kh++) {
                for (int kw = 0; kw < 3; kw++) {
                    acc = chip_mac(acc, rows[kh][(f + kw) * width + i], taps[(3 * kh + kw) * width + i]);
                }
            }
            out[f * width + i] = chip_round(acc, less);
        }
    }
}

void ai_engine_dw3x3_row_s8(int8_t *out, const int8_t *const *rows, const int8_t *taps, const int32_t *run)
{
    dw3x3(out, rows, taps, run, NULL);
}

void ai_engine_dw3x3_bias_row_s8(int8_t *out, const int8_t *const *rows, const int8_t *taps,
                                 const int32_t *run)
{
    dw3x3(out, rows, taps, run, AI_ENGINE_RUN_PTR(const int8_t *, run, 3));
}

void ai_engine_spread_row_s8(int8_t *out, const int8_t *in, int pixels)
{
    for (int p = 0; p < pixels; p++) {
        memset(out + p * kVector, in[p], kVector);
    }
}

void ai_engine_dwk_row_s8(int8_t *out, const int8_t *const *rows, const int8_t *taps, const int32_t *run)
{
    const int pixels = run[0];
    const int vectors = run[1];
    const int less = run[2] - 1;
    const int8_t *bias = AI_ENGINE_RUN_PTR(const int8_t *, run, 3);
    const int k = run[4];
    const int width = vectors * kVector;
    for (int f = 0; f < pixels; f++) {
        for (int v = 0; v < vectors; v++) {
            for (int c = 0; c < kVector; c++) {
                const int i = v * kVector + c;
                int32_t acc = chip_bias(bias + v * kBiasBytes, c);
                for (int j = 0; j < k; j++) {
                    acc = chip_mac(acc, rows[j][f * width + i], taps[(v * k + j) * kVector + c]);
                }
                out[f * width + i] = chip_round(acc, less);
            }
        }
    }
}

void ai_engine_pad_row_s8(int8_t *out, const int8_t *in, const int32_t *run)
{
    const int pixels = run[0];
    const int whole = run[1] * kVector;
    const int half = run[2] ? kHalf : 0;
    const int out_step = whole + (half ? kVector : 0);
    for (int p = 0; p < pixels; p++) {
        memcpy(out + p * out_step, in + p * (whole + half), whole + half);
    }
}

void ai_engine_dw_time_s8(int8_t *out, const int8_t *x, const int8_t *taps, const int32_t *run)
{
    const int rows = run[0];
    const int vectors = run[1];
    const int k = run[2];
    const int less = run[3] - 1;
    const int8_t *bias = AI_ENGINE_RUN_PTR(const int8_t *, run, 4);
    const int width = vectors * kVector;
    for (int t = 0; t < rows; t++) {
        for (int i = 0; i < width; i++) {
            int32_t acc = chip_bias(bias + (i / kVector) * kBiasBytes, i % kVector);
            for (int j = 0; j < k; j++) {
                acc = chip_mac(acc, x[(t + j) * width + i], taps[j * width + i]);
            }
            out[t * width + i] = chip_round(acc, less);
        }
    }
}
