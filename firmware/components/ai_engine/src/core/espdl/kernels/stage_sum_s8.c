#include "espdl_chip_kernels.h"
#include "espdl_kernels.hpp"

void ai_engine_stage_sum_row_s8(int8_t *row, const int8_t *x, const int8_t *weights, const int32_t *run)
{
    const int values = run[0] * 16;
    const int shift = run[1];
    const int store = run[2];
    for (int i = 0; i < values; i++) {
        const int32_t acc = chip_qacc((int32_t)x[i] * weights[i]);
        const int8_t product = shift > 0 ? chip_round(acc, shift - 1) : chip_sat8(acc);
        row[i] = store ? product : chip_adds8(row[i], product);
    }
}
