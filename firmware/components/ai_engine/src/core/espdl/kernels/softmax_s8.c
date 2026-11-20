#include "espdl_chip_kernels.h"
#include "espdl_kernels.hpp"

enum { kColumns = 8, kRows = 4 };

void ai_engine_softmax_sums_s8(float *sums, const int8_t *x, const float *table, int rows, int stride)
{
    for (int c = 0; c < kColumns; c++) {
        float sum = 0.0f;
        for (int r = 0; r < rows; r++) {
            sum += table[(uint8_t)x[r * stride + c]];
        }
        sums[c] = sum;
    }
}

void ai_engine_softmax_norm_s8(int8_t *y, const int8_t *x, const float *table, const float *scales, int rows,
                               const int32_t *strides)
{
    for (int r = 0; r < rows; r++) {
        for (int c = 0; c < kColumns; c++) {
            y[r * strides[1] + c] = chip_probability_to_s8(table[(uint8_t)x[r * strides[0] + c]] * scales[c]);
        }
    }
}

void ai_engine_softmax_row_sums_s8(float *sums, const int8_t *x, const float *table, int len, int stride)
{
    for (int r = 0; r < kRows; r++) {
        float sum = 0.0f;
        for (int i = 0; i < len; i++) {
            sum += table[(uint8_t)x[r * stride + i]];
        }
        sums[r] = sum;
    }
}
