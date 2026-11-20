#include "espdl_kernels.hpp"

void ai_engine_lut_s8(int8_t *values, int n, const int8_t *turned)
{
    for (int i = 0; i < n; i++) {
        values[i] = turned[(uint8_t)values[i]];
    }
}
