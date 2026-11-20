#include "espdl_chip_kernels.h"
#include "espdl_kernels.hpp"

enum { kGroup = 16 };

static int8_t product(int8_t a, int8_t b, int less)
{
    return chip_round(chip_qacc((int32_t)a * b), less);
}

void ai_engine_weighted_stats_row_s8(int16_t *sums, const int8_t *x, const int8_t *p, const int32_t *run)
{
    const int groups = run[0];
    for (int g = 0; g < groups; g++) {
        int16_t *at = sums + g * 2 * kGroup;
        for (int c = 0; c < kGroup; c++) {
            const int i = g * kGroup + c;
            const int8_t xx = product(x[i], x[i], run[1] - 1);
            at[c] = chip_sat16(at[c] + product(p[i], x[i], run[2] - 1));
            at[kGroup + c] = chip_sat16(at[kGroup + c] + product(p[i], xx, run[3] - 1));
        }
    }
}

void ai_engine_row_sums_s8(int16_t *sums, const int8_t *x, int groups)
{
    for (int i = 0; i < groups * kGroup; i++) {
        sums[i] = chip_sat16(sums[i] + x[i]);
    }
}

void ai_engine_centred_squares_row_s8(int16_t *sums, const int8_t *x, const int8_t *mean, const int32_t *run)
{
    for (int i = 0; i < run[0] * kGroup; i++) {
        const int8_t d = chip_subs8(x[i], mean[i]);
        sums[i] = chip_sat16(sums[i] + product(d, d, run[1] - 1));
    }
}
