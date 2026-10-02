#include <math.h>
#include <stdint.h>
#include <stdlib.h>

#include "command_ctc/ctc_score.h"
#include "parity.h"

enum { THRESHOLD_REJECT = 0, THRESHOLD_MARGIN, THRESHOLD_COUNT };
enum { DECISION_COMMAND = 0, DECISION_SCORE, DECISION_MARGIN, DECISION_GAP, DECISION_COUNT };
#define UNREACHED (-1.0e30f) // stands for -inf, which the comparators cannot difference

static void decision_row(const ai_engine_command_result_t *d, float *row)
{
    row[DECISION_COMMAND] = d->command;
    row[DECISION_SCORE] = d->score_permille;
    row[DECISION_MARGIN] = d->margin_permille;
    row[DECISION_GAP] = d->free_gap_permille;
}

static void mark_unreached(float *x, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        if (isinf(x[i]) && x[i] < 0.0f) { x[i] = UNREACHED; }
    }
}

static bool lexicon_of(const gold_tensor_t *units, const gold_tensor_t *n_variants,
                       const gold_tensor_t *n_units, ai_engine_lexicon_t *lex, uint8_t *ids)
{
    const size_t commands = units->dims[0], most = units->dims[1], longest = units->dims[2];
    float *u = malloc(commands * most * longest * sizeof(float));
    float *nv = malloc(commands * sizeof(float));
    float *nu = malloc(commands * most * sizeof(float));
    bool ok = u != NULL && nv != NULL && nu != NULL && commands <= AI_ENGINE_COMMANDS_MAX &&
              most <= AI_ENGINE_VARIANTS_MAX && parity_floats(units, u, commands * most * longest) &&
              parity_floats(n_variants, nv, commands) && parity_floats(n_units, nu, commands * most);
    lex->n_commands = (uint8_t)commands;
    for (size_t c = 0; ok && c < commands; c++) {
        lex->n_variants[c] = (uint8_t)nv[c];
        for (size_t v = 0; v < most; v++) {
            uint8_t *seq = ids + (c * most + v) * longest;
            for (size_t k = 0; k < longest; k++) {
                seq[k] = (uint8_t)u[(c * most + v) * longest + k];
            }
            lex->variants[c][v] = (ai_engine_seq_t){.n_units = (uint8_t)nu[c * most + v], .units = seq};
        }
    }
    free(u);
    free(nv);
    free(nu);
    return ok;
}

bool parity_command_ctc(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t logits, exponent, log_probs, frames, units, n_variants, n_units, thresholds, scores,
        decision;
    if (!parity_tensor(buf, len, "logits", &logits) || !parity_tensor(buf, len, "exponent", &exponent) ||
        !parity_tensor(buf, len, "log_probs", &log_probs) || !parity_tensor(buf, len, "frames", &frames) ||
        !parity_tensor(buf, len, "units", &units) || !parity_tensor(buf, len, "n_variants", &n_variants) ||
        !parity_tensor(buf, len, "n_units", &n_units) ||
        !parity_tensor(buf, len, "thresholds", &thresholds) || !parity_tensor(buf, len, "scores", &scores) ||
        !parity_tensor(buf, len, "decision", &decision)) {
        return false;
    }
    const size_t windows = log_probs.dims[0], longest = log_probs.dims[1], classes = log_probs.dims[2];
    const size_t commands = units.dims[0];
    if (logits.dims[0] != windows || logits.dims[1] != longest || logits.dims[2] != classes ||
        exponent.dims[0] != windows || frames.dims[0] != windows || thresholds.dims[0] != windows ||
        thresholds.dims[1] != THRESHOLD_COUNT || scores.dims[0] != windows || scores.dims[1] != commands ||
        decision.dims[0] != windows || decision.dims[1] != DECISION_COUNT) {
        return false;
    }
    float *raw = malloc(windows * longest * classes * sizeof(float));
    float *steps = malloc(windows * sizeof(float));
    int8_t *q = malloc(longest * classes);
    float *in = malloc(windows * longest * classes * sizeof(float));
    float *got_log_probs = calloc(windows * longest * classes, sizeof(float));
    float *lengths = malloc(windows * sizeof(float));
    float *limits = malloc(windows * THRESHOLD_COUNT * sizeof(float));
    float *want_scores = malloc(windows * commands * sizeof(float));
    float *want = malloc(windows * DECISION_COUNT * sizeof(float));
    float *got_scores = malloc(windows * commands * sizeof(float));
    float *got = malloc(windows * DECISION_COUNT * sizeof(float));
    ai_engine_lexicon_t *lex = calloc(1, sizeof(*lex));
    uint8_t *ids = malloc(units.dims[0] * units.dims[1] * units.dims[2]);
    void *work = malloc(ai_engine_command_ctc_work_bytes(classes, longest));
    bool ok = raw != NULL && steps != NULL && q != NULL && in != NULL && got_log_probs != NULL &&
              lengths != NULL && limits != NULL && want_scores != NULL && want != NULL &&
              got_scores != NULL && got != NULL && lex != NULL && ids != NULL && work != NULL &&
              parity_floats(&logits, raw, windows * longest * classes) &&
              parity_floats(&exponent, steps, windows) &&
              parity_floats(&log_probs, in, windows * longest * classes) &&
              parity_floats(&frames, lengths, windows) &&
              parity_floats(&thresholds, limits, windows * THRESHOLD_COUNT) &&
              parity_floats(&scores, want_scores, windows * commands) &&
              parity_floats(&decision, want, windows * DECISION_COUNT) &&
              lexicon_of(&units, &n_variants, &n_units, lex, ids);
    for (size_t w = 0; ok && w < windows; w++) {
        const size_t n = (size_t)lengths[w];
        float *mine = got_log_probs + w * longest * classes;
        for (size_t i = 0; i < n * classes; i++) {
            q[i] = (int8_t)raw[w * longest * classes + i];
        }
        ai_engine_command_result_t d;
        const float *limit = limits + w * THRESHOLD_COUNT;
        ok = ai_engine_command_ctc_log_probs(q, (int)steps[w], classes, n, mine) == ESP_OK &&
             ai_engine_command_ctc_decide(mine, classes, n, lex, (uint16_t)limit[THRESHOLD_REJECT],
                                          (uint16_t)limit[THRESHOLD_MARGIN], work, got_scores + w * commands,
                                          &d) == ESP_OK;
        decision_row(&d, got + w * DECISION_COUNT);
    }
    if (ok) {
        mark_unreached(want_scores, windows * commands);
        mark_unreached(got_scores, windows * commands);
        parity_report("command_ctc", case_name, "log_probs", in, got_log_probs, windows * longest * classes);
        parity_report("command_ctc", case_name, "scores", want_scores, got_scores, windows * commands);
        parity_report("command_ctc", case_name, "decision", want, got, windows * DECISION_COUNT);
    }
    free(raw);
    free(steps);
    free(q);
    free(in);
    free(got_log_probs);
    free(lengths);
    free(limits);
    free(want_scores);
    free(want);
    free(got_scores);
    free(got);
    free(lex);
    free(ids);
    free(work);
    return ok;
}
