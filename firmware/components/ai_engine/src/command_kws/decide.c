#include "command_kws/decide.h"

#include <math.h>
#include <stdbool.h>

#define PERMILLE 1000.0f

static uint16_t permille(float p)
{
    return (uint16_t)lrintf(p * PERMILLE);
}

esp_err_t ai_engine_command_kws_decide(const float *logits, size_t n_commands, uint16_t reject_permille,
                                       uint16_t margin_permille, float *probs,
                                       ai_engine_command_kws_decision_t *out)
{
    if (logits == NULL || probs == NULL || out == NULL || n_commands == 0 || n_commands > INT16_MAX) {
        return ESP_ERR_INVALID_ARG;
    }
    const size_t n_classes = n_commands + AI_ENGINE_COMMAND_KWS_EXTRA_CLASSES;
    float top = logits[0];
    for (size_t i = 1; i < n_classes; i++) {
        if (logits[i] > top) { top = logits[i]; }
    }
    float total = 0.0f;
    for (size_t i = 0; i < n_classes; i++) {
        // exp in double rounded to float, as the Python mirror takes it, so both agree bit for bit.
        probs[i] = (float)exp((double)(logits[i] - top));
        total += probs[i];
    }
    size_t best = 0;
    for (size_t i = 0; i < n_classes; i++) {
        probs[i] /= total;
        if (probs[i] > probs[best]) { best = i; }
    }
    float second = 0.0f;
    for (size_t i = 0; i < n_classes; i++) {
        if (i != best && probs[i] > second) { second = probs[i]; }
    }
    const uint16_t score = permille(probs[best]);
    const uint16_t margin = permille(probs[best] - second);
    const bool accepted = best < n_commands && score >= reject_permille && margin >= margin_permille;
    *out = (ai_engine_command_kws_decision_t){
        .command = accepted ? (int16_t)best : AI_ENGINE_COMMAND_KWS_REJECTED,
        .score_permille = score,
        .margin_permille = margin,
        .rest_permille = permille(probs[n_commands] + probs[n_commands + 1]),
    };
    return ESP_OK;
}
