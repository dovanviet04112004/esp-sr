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
            const float scaled = table[(uint8_t)x[r * strides[0] + c]] * scales[c];
            const int32_t whole = (int32_t)(scaled + 0.5f);
            y[r * strides[1] + c] = (int8_t)(whole < INT8_MAX ? whole : INT8_MAX);
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
