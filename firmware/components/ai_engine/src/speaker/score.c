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
static int16_t milli_of(float dot, float mean_print, float std_print, float mean_unit, float std_unit)
{
    const float z_print = (dot - mean_print) / std_print;
    const float z_unit = (dot - mean_unit) / std_unit;
    const float scaled = (z_print + z_unit) * 0.5f * PERMILLE;
    if (scaled > (float)AI_ENGINE_SPEAKER_Z_LIMIT) { return AI_ENGINE_SPEAKER_Z_LIMIT; }
    if (scaled < -(float)AI_ENGINE_SPEAKER_Z_LIMIT) { return -AI_ENGINE_SPEAKER_Z_LIMIT; }
    return (int16_t)lrintf(scaled);
}

static bool beats(const float *scores, uint16_t row, uint16_t other)
{
    return scores[row] > scores[other] || (scores[row] == scores[other] && row < other);
}

// order[0..top) ends holding the rows of the top highest scores, in row order.
static void top_rows(const float *scores, uint16_t *order, size_t rows, size_t top)
{
    for (size_t r = 0; r < rows; r++) {
        order[r] = (uint16_t)r;
    }
    for (size_t i = 0; i < top; i++) {
        size_t best = i;
        for (size_t j = i + 1; j < rows; j++) {
            if (beats(scores, order[j], order[best])) { best = j; }
        }
        const uint16_t picked = order[best];
        order[best] = order[i];
        order[i] = picked;
    }
    for (size_t i = 1; i < top; i++) {
        const uint16_t row = order[i];
        size_t j = i;
        for (; j > 0 && order[j - 1] > row; j--) {
            order[j] = order[j - 1];
        }
        order[j] = row;
    }
}

int16_t ai_engine_speaker_score_permille(const float *unit, const float *voiceprint, size_t dims)
{
    return permille_of(dot_of(unit, voiceprint, dims));
}

void ai_engine_speaker_norm_of(const float *vector, const float *cohort, size_t rows, size_t dims, size_t top,
                               float *scores, uint16_t *order, float *mean, float *std)
{
    for (size_t r = 0; r < rows; r++) {
        scores[r] = dot_of(cohort + r * dims, vector, dims);
    }
    top_rows(scores, order, rows, top);
    float total = 0.0f;
    for (size_t i = 0; i < top; i++) {
        total += scores[order[i]];
    }
    const float m = total / (float)top;
    float squares = 0.0f;
    for (size_t i = 0; i < top; i++) {
        const float gap = scores[order[i]] - m;
        squares += gap * gap;
    }
    const float s = sqrtf(squares / (float)top);
    *mean = m;
    *std = s == 0.0f ? 1.0f : s;
}

int16_t ai_engine_speaker_best_as(const float *unit, const float *prints, const float *means,
                                  const float *stds, size_t n, size_t dims, float unit_mean, float unit_std,
                                  size_t *index, int16_t *cosine_permille)
{
    int16_t best = -AI_ENGINE_SPEAKER_Z_LIMIT;
    *index = 0;
    *cosine_permille = -AI_ENGINE_SPEAKER_SCORE_LIMIT;
    for (size_t k = 0; k < n; k++) {
        const float dot = dot_of(unit, prints + k * dims, dims);
        const int16_t score = milli_of(dot, means[k], stds[k], unit_mean, unit_std);
        if (k == 0 || score > best) {
            best = score;
            *index = k;
            *cosine_permille = permille_of(dot);
        }
    }
    return best;
}

bool ai_engine_speaker_accepts(int16_t milli_z, int16_t threshold_milli_z)
{
    return milli_z >= threshold_milli_z;
}
