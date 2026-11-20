#include "espdl_kernels.hpp"

void ai_engine_lut_s16(int16_t *values, int n, const int16_t *middle, int shift)
{
    const int32_t half = shift > 0 ? 1 << (shift - 1) : 0;
    for (int i = 0; i < n; i++) {
        values[i] = middle[((int32_t)values[i] + half) >> shift];
    }
}
