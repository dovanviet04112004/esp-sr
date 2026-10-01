#include "command_ctc/ctc_score.h"

#include <math.h>
#include <stdbool.h>
#include <string.h>

#define BLANK 0
#define MILLI 1000.0f
#define FIELD_MAX 65535 // the uint16 fields of ai_engine_command_result_t
#define STATES_MAX (2 * AI_ENGINE_COMMAND_CTC_UNITS_MAX + 1)

static float log_sum_exp(const float *terms, size_t n)
{
    float top = terms[0];
    for (size_t i = 1; i < n; i++) {
        if (terms[i] > top) { top = terms[i]; }
    }
    if (top == -INFINITY) { return -INFINITY; }
    float total = 0.0f;
    for (size_t i = 0; i < n; i++) {
        // exp and log in double rounded to float, as the Python mirror takes them, so both agree bit for bit.
        total += (float)exp((double)(terms[i] - top));
    }
    return top + (float)log((double)total);
}

static float sequence_score(const float *log_probs, size_t n_classes, size_t n_frames,
                            const ai_engine_seq_t *seq)
{
    uint8_t labels[STATES_MAX];
    size_t n = 0;
    labels[n++] = BLANK;
    for (size_t k = 0; k < seq->n_units; k++) {
        labels[n++] = (uint8_t)(seq->units[k] + 1);
        labels[n++] = BLANK;
    }
    float alpha[STATES_MAX], before[STATES_MAX];
    for (size_t s = 0; s < n; s++) {
        alpha[s] = -INFINITY;
    }
    alpha[0] = log_probs[BLANK];
    if (n > 1) { alpha[1] = log_probs[labels[1]]; }
    for (size_t t = 1; t < n_frames; t++) {
        memcpy(before, alpha, n * sizeof(float));
        const float *frame = log_probs + t * n_classes;
        for (size_t s = 0; s < n; s++) {
            float terms[3];
            size_t k = 0;
            terms[k++] = before[s];
            if (s >= 1) { terms[k++] = before[s - 1]; }
            if (s >= 2 && labels[s] != BLANK && labels[s] != labels[s - 2]) { terms[k++] = before[s - 2]; }
            alpha[s] = log_sum_exp(terms, k) + frame[labels[s]];
        }
    }
    const float tail[2] = {alpha[n - 1], n > 1 ? alpha[n - 2] : -INFINITY};
    return log_sum_exp(tail, n > 1 ? 2 : 1) / (float)n_frames;
}

static float free_score(const float *log_probs, size_t n_classes, size_t n_frames)
{
    float total = 0.0f;
    for (size_t t = 0; t < n_frames; t++) {
        const float *frame = log_probs + t * n_classes;
        float top = frame[0];
        for (size_t c = 1; c < n_classes; c++) {
            if (frame[c] > top) { top = frame[c]; }
        }
        total += top;
    }
    return total / (float)n_frames;
}

static uint16_t milli(float x)
{
    const float scaled = x * MILLI;
    if (!(scaled < (float)FIELD_MAX)) { return FIELD_MAX; }
    if (scaled <= 0.0f) { return 0; }
    return (uint16_t)lrintf(scaled);
}

static esp_err_t check(const ai_engine_lexicon_t *lexicon, size_t n_classes)
{
    for (size_t c = 0; c < lexicon->n_commands; c++) {
        if (lexicon->n_variants[c] == 0 || lexicon->n_variants[c] > AI_ENGINE_VARIANTS_MAX) {
            return ESP_ERR_INVALID_ARG;
        }
        for (size_t v = 0; v < lexicon->n_variants[c]; v++) {
            const ai_engine_seq_t *seq = &lexicon->variants[c][v];
            if (seq->n_units > AI_ENGINE_COMMAND_CTC_UNITS_MAX) { return ESP_ERR_INVALID_SIZE; }
            for (size_t k = 0; k < seq->n_units; k++) {
                if ((size_t)seq->units[k] + 1 >= n_classes) { return ESP_ERR_INVALID_ARG; }
            }
        }
    }
    return ESP_OK;
}

esp_err_t ai_engine_command_ctc_decide(const float *log_probs, size_t n_classes, size_t n_frames,
                                       const ai_engine_lexicon_t *lexicon, uint16_t reject, uint16_t margin,
                                       float *scores, ai_engine_command_result_t *out)
{
    if (log_probs == NULL || lexicon == NULL || out == NULL || n_classes < 2 || n_frames == 0 ||
        lexicon->n_commands == 0 || lexicon->n_commands > AI_ENGINE_COMMANDS_MAX) {
        return ESP_ERR_INVALID_ARG;
    }
    const esp_err_t err = check(lexicon, n_classes);
    if (err != ESP_OK) { return err; }
    size_t best = 0;
    float best_score = -INFINITY, second = -INFINITY;
    for (size_t c = 0; c < lexicon->n_commands; c++) {
        float score = -INFINITY;
        for (size_t v = 0; v < lexicon->n_variants[c]; v++) {
            const float s = sequence_score(log_probs, n_classes, n_frames, &lexicon->variants[c][v]);
            if (s > score) { score = s; }
        }
        if (scores != NULL) { scores[c] = score; }
        if (score > best_score) {
            second = best_score;
            best_score = score;
            best = c;
        } else if (score > second) {
            second = score;
        }
    }
    const bool reached = best_score > -INFINITY;
    const uint16_t gap = reached ? milli(free_score(log_probs, n_classes, n_frames) - best_score) : FIELD_MAX;
    const uint16_t lead = second > -INFINITY ? milli(best_score - second) : FIELD_MAX;
    const bool accepted = reached && gap <= reject && lead >= margin;
    *out = (ai_engine_command_result_t){
        .command = accepted ? (int16_t)best : AI_ENGINE_COMMAND_CTC_REJECTED,
        .score_permille = reached ? milli((float)exp((double)best_score)) : 0,
        .margin_permille = lead,
        .free_gap_permille = gap,
    };
    return ESP_OK;
}
