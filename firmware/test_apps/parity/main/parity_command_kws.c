#include <stdlib.h>

#include "command_kws/decide.h"
#include "parity.h"

enum { THRESHOLD_REJECT = 0, THRESHOLD_MARGIN, THRESHOLD_COUNT };
enum { DECISION_COMMAND = 0, DECISION_SCORE, DECISION_MARGIN, DECISION_REST, DECISION_COUNT };

static void decision_row(const ai_engine_command_kws_decision_t *d, float *row)
{
    row[DECISION_COMMAND] = d->command;
    row[DECISION_SCORE] = d->score_permille;
    row[DECISION_MARGIN] = d->margin_permille;
    row[DECISION_REST] = d->rest_permille;
}

bool parity_command_kws(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t n_commands, logits, thresholds, probs, decision;
    float commands = 0.0f;
    if (!parity_tensor(buf, len, "n_commands", &n_commands) || !parity_tensor(buf, len, "logits", &logits) ||
        !parity_tensor(buf, len, "thresholds", &thresholds) || !parity_tensor(buf, len, "probs", &probs) ||
        !parity_tensor(buf, len, "decision", &decision) || !parity_floats(&n_commands, &commands, 1)) {
        return false;
    }
    const size_t rows = logits.dims[0], n_classes = logits.dims[1];
    if (commands < 1.0f || (size_t)commands + AI_ENGINE_COMMAND_KWS_EXTRA_CLASSES != n_classes ||
        probs.dims[0] != rows || thresholds.dims[0] != rows || thresholds.dims[1] != THRESHOLD_COUNT ||
        decision.dims[0] != rows || decision.dims[1] != DECISION_COUNT) {
        return false;
    }
    float *in = malloc(rows * n_classes * sizeof(float));
    float *limits = malloc(rows * THRESHOLD_COUNT * sizeof(float));
    float *want = malloc(rows * DECISION_COUNT * sizeof(float));
    float *got_probs = malloc(rows * n_classes * sizeof(float));
    float *got = malloc(rows * DECISION_COUNT * sizeof(float));
    bool ok = in != NULL && limits != NULL && want != NULL && got_probs != NULL && got != NULL &&
              parity_floats(&logits, in, rows * n_classes) &&
              parity_floats(&thresholds, limits, rows * THRESHOLD_COUNT) &&
              parity_floats(&decision, want, rows * DECISION_COUNT);
    for (size_t r = 0; ok && r < rows; r++) {
        ai_engine_command_kws_decision_t d;
        const float *limit = limits + r * THRESHOLD_COUNT;
        ok = ai_engine_command_kws_decide(
                 in + r * n_classes, (size_t)commands, (uint16_t)limit[THRESHOLD_REJECT],
                 (uint16_t)limit[THRESHOLD_MARGIN], got_probs + r * n_classes, &d) == ESP_OK;
        decision_row(&d, got + r * DECISION_COUNT);
    }
    if (ok) {
        parity_report("command_kws", case_name, "probs", probs.data, got_probs, rows * n_classes);
        parity_report("command_kws", case_name, "decision", want, got, rows * DECISION_COUNT);
    }
    free(in);
    free(limits);
    free(want);
    free(got_probs);
    free(got);
    return ok;
}
