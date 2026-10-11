#include "speaker/score.h"

#include <math.h>
#include <string.h>

#define PERMILLE 1000.0f

static void to_unit(float *v, size_t dims)
{
    float total = 0.0f;
    for (size_t i = 0; i < dims; i++) {
        total += v[i] * v[i];
    }
    const float norm = sqrtf(total);
    if (norm == 0.0f) { return; }
    for (size_t i = 0; i < dims; i++) {
        v[i] = v[i] / norm;
    }
}

void ai_engine_speaker_unit(const int8_t *raw, size_t dims, int exponent, float *unit)
{
    const float step = ldexpf(1.0f, exponent);
    for (size_t i = 0; i < dims; i++) {
        unit[i] = (float)raw[i] * step;
    }
    to_unit(unit, dims);
}

void ai_engine_speaker_fold(const float *units, size_t n, size_t dims, float *voiceprint)
{
    memset(voiceprint, 0, dims * sizeof(float));
    for (size_t k = 0; k < n; k++) {
        for (size_t i = 0; i < dims; i++) {
            voiceprint[i] += units[k * dims + i];
        }
    }
    to_unit(voiceprint, dims);
}

int16_t ai_engine_speaker_score_permille(const float *unit, const float *voiceprint, size_t dims)
{
    float dot = 0.0f;
    for (size_t i = 0; i < dims; i++) {
        dot += unit[i] * voiceprint[i];
    }
    const long permille = lrintf(dot * PERMILLE);
    if (permille > AI_ENGINE_SPEAKER_SCORE_LIMIT) { return AI_ENGINE_SPEAKER_SCORE_LIMIT; }
    return (int16_t)(permille < -AI_ENGINE_SPEAKER_SCORE_LIMIT ? -AI_ENGINE_SPEAKER_SCORE_LIMIT : permille);
}

int16_t ai_engine_speaker_best_permille(const float *unit, const float *prints, size_t n, size_t dims,
                                        size_t *index)
{
    int16_t top = -AI_ENGINE_SPEAKER_SCORE_LIMIT;
    *index = 0;
    for (size_t k = 0; k < n; k++) {
        const int16_t s = ai_engine_speaker_score_permille(unit, prints + k * dims, dims);
        if (k == 0 || s > top) {
            top = s;
            *index = k;
        }
    }
    return top;
}

bool ai_engine_speaker_accepts(int16_t score_permille, int16_t threshold_permille)
{
    return score_permille >= threshold_permille;
}
