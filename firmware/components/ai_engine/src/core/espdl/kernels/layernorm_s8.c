#include <math.h>
#include <string.h>

#include "espdl_chip_kernels.h"
#include "espdl_kernels.hpp"

enum { kLanes = 2 };

static float float_of(int32_t bits)
{
    float f;
    memcpy(&f, &bits, sizeof(f));
    return f;
}

static uint32_t bits_of(float f)
{
    uint32_t bits;
    memcpy(&bits, &f, sizeof(bits));
    return bits;
}

void ai_engine_ln_deviations_s8(float *dev, float *variances, const int8_t *x, int pairs, const int32_t *run)
{
    const int n = run[0];
    const float inv_n = float_of(run[3]);
    const float count = float_of(run[4]);
    const float scale = float_of(run[5]);
    const float epsilon = float_of(run[6]);
    for (int p = 0; p < pairs; p++) {
        const int8_t *lane[kLanes] = {x + 2 * p * n, x + 2 * p * n + n};
        float mean[kLanes];
        float squares[kLanes] = {0.0f, 0.0f};
        for (int k = 0; k < kLanes; k++) {
            int32_t sum = 0;
            for (int c = 0; c < n; c++) {
                sum += lane[k][c];
            }
            const float s = (float)sum;
            const float q = s * inv_n;
            mean[k] = fmaf(fmaf(-q, count, s), inv_n, q);
        }
        for (int c = 0; c < n; c++) {
            for (int k = 0; k < kLanes; k++) {
                const float d = (float)lane[k][c] - mean[k];
                dev[(p * n + c) * kLanes + k] = d;
                squares[k] = fmaf(d, d, squares[k]);
            }
        }
        for (int k = 0; k < kLanes; k++) {
            variances[kLanes * p + k] = fmaf(scale, squares[k], epsilon);
        }
    }
}

void ai_engine_ln_scales_s8(float *scales, const float *variances, const int32_t *run, int pairs)
{
    const float in_scale = float_of(run[7]);
    const float window = float_of(run[8]);
    const float half = float_of(run[9]);
    for (int i = 0; i < kLanes * pairs; i++) {
        const float x = variances[i];
        const float s0 = sqrtf(x);
        const float r = fmaf(-s0, s0, x);
        const uint32_t root_bits = bits_of(s0);
        const float half_ulp = float_of((int32_t)((root_bits & 0x7f800000u) - 0x0c000000u));
        const float h = s0 * half_ulp;
        const float ulp = half_ulp + half_ulp;
        float q = s0;
        if (h < r) { q = s0 + ulp; }
        if (r < -h) { q = s0 - ulp; }
        const float root = (q + s0) * half;
        const float w = h * window;
        const float off = fabsf(fabsf(r + w) - h);
        const float inverse = 1.0f / root;
        // A root esp-dl's own Newton may round apart from, or a power of 2, is left to the caller as 0.
        const int left = off <= w || (root_bits << 9) == 0;
        scales[i] = left ? 0.0f : inverse * in_scale;
    }
}

void ai_engine_ln_outputs_s8(int8_t *y, const float *dev, const float *scales, const float *gb, int pairs,
                             const int32_t *run)
{
    const int n = run[0];
    for (int p = 0; p < pairs; p++) {
        for (int k = 0; k < kLanes; k++) {
            const float scale = scales[kLanes * p + k];
            for (int c = 0; c < n; c++) {
                float t = gb[2 * c] * scale;
                t = t * dev[(p * n + c) * kLanes + k];
                t = t + gb[2 * c + 1];
                y[(kLanes * p + k) * n + c] = chip_to_s8(t);
            }
        }
    }
}
