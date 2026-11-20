#include <math.h>

#include "espdl_chip_kernels.h"
#include "espdl_kernels.hpp"

enum { kTable = 256 };

void ai_engine_gn_sums_s8(int32_t *sums, const int8_t *x, const int32_t *run)
{
    const int frames = run[0];
    const int stride = run[1];
    const int width = run[2] / 8 * 8;
    for (int g = 0; g < run[3]; g++) {
        int32_t sum = 0;
        for (int f = 0; f < frames; f++) {
            for (int c = 0; c < width; c++) {
                sum += x[g * run[2] + f * stride + c];
            }
        }
        sums[g] = sum;
    }
}

// Channel after channel, frame after frame, each value fused in repeat times: the PIE kernel's chains
// exactly.
static void gn_squares(float *squares, const int8_t *x, const float *deviations, const int32_t *run,
                       int repeat)
{
    const int frames = run[0];
    const int stride = run[1];
    const int width = run[2];
    for (int k = 0; k < 2; k++) {
        float sum = 0.0f;
        for (int c = 0; c < width; c++) {
            for (int f = 0; f < frames; f++) {
                const float d = deviations[k * kTable + (uint8_t)x[k * width + f * stride + c]];
                for (int r = 0; r < repeat; r++) {
                    sum = fmaf(d, d, sum);
                }
            }
        }
        squares[k] = sum;
    }
}

void ai_engine_gn_squares_s8(float *squares, const int8_t *x, const float *deviations, const int32_t *run)
{
    gn_squares(squares, x, deviations, run, 1);
}

void ai_engine_gn_squares_x2_s8(float *squares, const int8_t *x, const float *deviations, const int32_t *run)
{
    gn_squares(squares, x, deviations, run, 2);
}

void ai_engine_gn_squares_x4_s8(float *squares, const int8_t *x, const float *deviations, const int32_t *run)
{
    gn_squares(squares, x, deviations, run, 4);
}

void ai_engine_gn_outputs_s8(int8_t *y, const int8_t *x, const int8_t *tables, const int32_t *run)
{
    const int frames = run[0];
    const int columns = run[1];
    const int width = run[2];
    const int groups = run[3];
    const int less = run[4];
    const int8_t *weights = AI_ENGINE_RUN_PTR(const int8_t *, run, 5);
    const int8_t *bias = AI_ENGINE_RUN_PTR(const int8_t *, run, 6);
    int8_t *norms = AI_ENGINE_RUN_PTR(int8_t *, run, 7);
    const int stride = run[8];
    for (int f = 0; f < frames; f++) {
        const int8_t *in = x + f * stride;
        for (int g = 0; g < groups; g++) {
            for (int c = 0; c < width; c++) {
                norms[g * width + c] = tables[g * kTable + (uint8_t)in[g * width + c]];
            }
        }
        int8_t *out = y + f * stride;
        for (int i = 0; i < columns; i++) {
            out[i] = chip_adds8(chip_round(chip_qacc((int32_t)norms[i] * weights[i]), less), bias[i]);
        }
    }
}
