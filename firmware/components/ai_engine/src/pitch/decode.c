#include "pitch/decode.h"

#include <math.h>

#define F_MIN_HZ 46.875 // the network's lowest step
#define F_MAX_HZ 2093.75
#define NEIGHBOURS 3 // the most likely step and one each side

bool ai_engine_pitch_decoder_init(ai_engine_pitch_decoder_t *d, float f_min_hz, float f_max_hz,
                                  int logit_exponent, int sure_exponent)
{
    d->lo = -1;
    d->hi = -1;
    for (int i = 0; i < AI_ENGINE_PITCH_BINS; i++) {
        const float hz = (float)(F_MIN_HZ * pow(F_MAX_HZ / F_MIN_HZ, (double)i / (AI_ENGINE_PITCH_BINS - 1)));
        d->steps_log[i] = (float)log((double)hz);
        if (f_min_hz <= hz && f_max_hz >= hz) {
            d->lo = d->lo < 0 ? i : d->lo;
            d->hi = i;
        }
    }
    const double logit_step = ldexp(1.0, logit_exponent);
    const double sure_step = ldexp(1.0, sure_exponent);
    for (int k = 0; k < AI_ENGINE_PITCH_INT8_STEPS; k++) {
        d->weights[k] = (float)exp(-(double)k * logit_step);
        const double q = (double)(k - AI_ENGINE_PITCH_INT8_STEPS / 2);
        d->sigmoids[k] = (float)(1.0 / (1.0 + exp(-q * sure_step)));
    }
    return d->lo >= 0;
}

void ai_engine_pitch_decode(const ai_engine_pitch_decoder_t *d, const int8_t *logits, size_t stride,
                            int8_t sure, float *log_f0, float *confidence)
{
    int most = d->lo;
    for (int i = d->lo + 1; i <= d->hi; i++) {
        if (logits[i * stride] > logits[most * stride]) { most = i; }
    }
    int all = 0;
    for (int i = 1; i < AI_ENGINE_PITCH_BINS; i++) {
        if (logits[i * stride] > logits[all * stride]) { all = i; }
    }
    float e[NEIGHBOURS];
    float steps[NEIGHBOURS];
    for (int j = 0; j < NEIGHBOURS; j++) {
        const int at = most - 1 + j;
        const int clamped = at < d->lo ? d->lo : at > d->hi ? d->hi : at;
        e[j] = at == clamped ? d->weights[logits[most * stride] - logits[at * stride]] : 0.0f;
        steps[j] = d->steps_log[clamped];
    }
    const float total = (e[0] + e[1]) + e[2];
    *log_f0 = ((e[0] / total) * steps[0] + (e[1] / total) * steps[1]) + (e[2] / total) * steps[2];
    *confidence = all >= d->lo && all <= d->hi ? d->sigmoids[sure + AI_ENGINE_PITCH_INT8_STEPS / 2] : 0.0f;
}
