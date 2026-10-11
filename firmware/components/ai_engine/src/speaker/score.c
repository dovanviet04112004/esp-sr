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

static float dot_of(const float *a, const float *b, size_t dims)
{
    float total = 0.0f;
    for (size_t i = 0; i < dims; i++) {
        total += a[i] * b[i];
    }
    return total;
}

static int16_t permille_of(float dot)
{
    const long permille = lrintf(dot * PERMILLE);
    if (permille > AI_ENGINE_SPEAKER_SCORE_LIMIT) { return AI_ENGINE_SPEAKER_SCORE_LIMIT; }
    return (int16_t)(permille < -AI_ENGINE_SPEAKER_SCORE_LIMIT ? -AI_ENGINE_SPEAKER_SCORE_LIMIT : permille);
}

// Clamped ahead of lrintf, which a value past long leaves undefined; the mirror rounds then clips, the same
// here.
static int16_t milli_z_of(float dot, float mean, float std)
{
    const float scaled = (dot - mean) / std * PERMILLE;
    if (scaled > (float)AI_ENGINE_SPEAKER_Z_LIMIT) { return AI_ENGINE_SPEAKER_Z_LIMIT; }
    if (scaled < -(float)AI_ENGINE_SPEAKER_Z_LIMIT) { return -AI_ENGINE_SPEAKER_Z_LIMIT; }
    return (int16_t)lrintf(scaled);
}

int16_t ai_engine_speaker_score_permille(const float *unit, const float *voiceprint, size_t dims)
{
    return permille_of(dot_of(unit, voiceprint, dims));
}

void ai_engine_speaker_norm_of(const float *voiceprint, const float *cohort, size_t rows, size_t dims,
                               float *mean, float *std)
{
    float total = 0.0f;
    for (size_t r = 0; r < rows; r++) {
        total += dot_of(cohort + r * dims, voiceprint, dims);
    }
    const float m = total / (float)rows;
    float squares = 0.0f;
    for (size_t r = 0; r < rows; r++) {
        const float gap = dot_of(cohort + r * dims, voiceprint, dims) - m;
        squares += gap * gap;
    }
    const float s = sqrtf(squares / (float)rows);
    *mean = m;
    *std = s == 0.0f ? 1.0f : s;
}

int16_t ai_engine_speaker_best_z(const float *unit, const float *prints, const float *means,
                                 const float *stds, size_t n, size_t dims, size_t *index,
                                 int16_t *cosine_permille)
{
    int16_t top = -AI_ENGINE_SPEAKER_Z_LIMIT;
    *index = 0;
    *cosine_permille = -AI_ENGINE_SPEAKER_SCORE_LIMIT;
    for (size_t k = 0; k < n; k++) {
        const float dot = dot_of(unit, prints + k * dims, dims);
        const int16_t z = milli_z_of(dot, means[k], stds[k]);
        if (k == 0 || z > top) {
            top = z;
            *index = k;
            *cosine_permille = permille_of(dot);
        }
    }
    return top;
}

bool ai_engine_speaker_accepts(int16_t milli_z, int16_t threshold_milli_z)
{
    return milli_z >= threshold_milli_z;
}
