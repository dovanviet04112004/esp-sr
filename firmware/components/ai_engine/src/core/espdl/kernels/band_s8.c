#include <stddef.h>

#include "espdl_chip_kernels.h"
#include "espdl_kernels.hpp"

enum { kOutputs = 16, kLanes = 12, kBandSlots = 3 };

// A band's taps over one input column: each tap a vector of lanes, each lane a weight row of 16 outputs.
static void taps_into(int32_t *acc, const int8_t *in, int step, const int8_t *weights, int taps, int lanes)
{
    for (int k = 0; k < taps; k++) {
        const int8_t *x = in + k * step;
        for (int lane = 0; lane < lanes; lane++) {
            const int8_t *w = weights + (k * lanes + lane) * kOutputs;
            for (int n = 0; n < kOutputs; n++) {
                acc[n] = chip_mac(acc[n], x[lane], w[n]);
            }
        }
    }
}

// The rounded sum, the residual added as RequantizeLinear rounds it (less < 0: as it is), then ReLU.
static void band_out(int8_t *out, const int32_t *acc, int less, const int8_t *residual, int residual_less,
                     int relu)
{
    for (int n = 0; n < kOutputs; n++) {
        int8_t v = chip_round(acc[n], less);
        if (residual != NULL) {
            v = chip_adds8(v, residual_less < 0 ? residual[n] : chip_round(residual[n], residual_less));
        }
        out[n] = relu && v < 0 ? 0 : v;
    }
}

void ai_engine_band_conv_s8(int8_t *out, const int8_t *const *columns, const int8_t *weights,
                            const int32_t *run)
{
    const int bands = run[0];
    const int32_t *table = AI_ENGINE_RUN_PTR(const int32_t *, run, 1);
    const int8_t *bias = AI_ENGINE_RUN_PTR(const int8_t *, run, 7);
    for (int b = 0; b < bands; b++, table += kBandSlots, out += run[5]) {
        int32_t acc[kOutputs];
        for (int n = 0; n < kOutputs; n++) {
            acc[n] = chip_bias(bias, n);
        }
        for (int t = 0; t < run[2]; t++) {
            if (columns[t] == NULL) { continue; }
            taps_into(acc, columns[t] + table[0], run[4], weights + run[3] * t + table[1], table[2], kLanes);
        }
        const int8_t *residual = run[8] != 0 ? out + run[9] : NULL;
        band_out(out, acc, run[6] - 1, residual, run[8] - 2, run[10]);
    }
}

static void band_fast(int8_t *out, const int32_t *run, int time_taps, int lanes, int residual, int relu)
{
    const int bands = run[0];
    const int32_t *table = AI_ENGINE_RUN_PTR(const int32_t *, run, 1);
    const int8_t *bias = AI_ENGINE_RUN_PTR(const int8_t *, run, 5);
    for (int b = 0; b < bands; b++, table += kBandSlots, out += run[3]) {
        int32_t acc[kOutputs];
        for (int n = 0; n < kOutputs; n++) {
            acc[n] = chip_bias(bias, n);
        }
        for (int t = 0; t < time_taps; t++) {
            const int8_t *column = AI_ENGINE_RUN_PTR(const int8_t *, run, 8 + t);
            const int8_t *w = AI_ENGINE_RUN_PTR(const int8_t *, run, 11 + t);
            taps_into(acc, column + table[0], run[2], w + table[1], table[2], lanes);
        }
        band_out(out, acc, run[4], residual ? out + run[7] : NULL, run[6], relu);
    }
}

void ai_engine_band_k1_s8(int8_t *out, const int32_t *run)
{
    band_fast(out, run, 1, kLanes, 0, 1);
}

void ai_engine_band_k1_res_s8(int8_t *out, const int32_t *run)
{
    band_fast(out, run, 1, kLanes, 1, 1);
}

void ai_engine_band_k1_linear_s8(int8_t *out, const int32_t *run)
{
    band_fast(out, run, 1, kLanes, 0, 0);
}

void ai_engine_band_k3_s8(int8_t *out, const int32_t *run)
{
    band_fast(out, run, 3, kLanes, 0, 1);
}

void ai_engine_band_k3_res_s8(int8_t *out, const int32_t *run)
{
    band_fast(out, run, 3, kLanes, 1, 1);
}

void ai_engine_band_k3_l3_s8(int8_t *out, const int32_t *run)
{
    band_fast(out, run, 3, 3, 0, 1);
}
