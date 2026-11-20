#include "espdl_chip_kernels.h"
#include "espdl_kernels.hpp"

enum { kOutputs = 16, kRowInputs = 9 };

static void stem(int8_t *out, const int32_t *run, int rows)
{
    const int pixels = run[0];
    const int step = run[1];
    const int8_t *window[3] = {AI_ENGINE_RUN_PTR(const int8_t *, run, 2),
                               AI_ENGINE_RUN_PTR(const int8_t *, run, 3),
                               AI_ENGINE_RUN_PTR(const int8_t *, run, 4)};
    const int8_t *weights = AI_ENGINE_RUN_PTR(const int8_t *, run, 5);
    const int less = run[6];
    const int8_t *bias = AI_ENGINE_RUN_PTR(const int8_t *, run, 7);
    for (int p = 0; p < pixels; p++) {
        for (int n = 0; n < kOutputs; n++) {
            int32_t acc = chip_bias(bias, n);
            for (int r = 0; r < rows; r++) {
                for (int j = 0; j < kRowInputs; j++) {
                    acc =
                        chip_mac(acc, window[r][p * step + j], weights[(r * kRowInputs + j) * kOutputs + n]);
                }
            }
            out[p * kOutputs + n] = chip_round(acc, less);
        }
    }
}

void ai_engine_stem3_s8(int8_t *out, const int32_t *run)
{
    stem(out, run, 3);
}

void ai_engine_stem3_top_s8(int8_t *out, const int32_t *run)
{
    stem(out, run, 2);
}
