#include <stdint.h>
#include <stdlib.h>

#include "command_ctc/ctc_score.h"
#include "command_rnnt/rnnt_search.h"
#include "parity.h"
#include "parity_command.h"

enum { LIMIT_REJECT = 0, LIMIT_MARGIN, LIMIT_COUNT };

// The golden set's stand-in for the joiner: frame t's logits plus the pulls of the context's two classes.
typedef struct {
    const float *frames; // the window's (frames, classes) int8 values
    const float *first;  // (classes + 1, classes): older class' pull
    const float *last;   // (classes + 1, classes): newer class' pull
    size_t classes;
    int exponent;
    int8_t *q;
} table_t;

static esp_err_t table_rows(void *ctx, size_t frame, const uint8_t *contexts, size_t n, float *rows)
{
    const table_t *t = ctx;
    for (size_t j = 0; j < n; j++) {
        const uint8_t *context = contexts + j * AI_ENGINE_COMMAND_RNNT_CONTEXT;
        for (size_t k = 0; k < t->classes; k++) {
            const int32_t sum = (int32_t)t->frames[frame * t->classes + k] +
                                (int32_t)t->first[context[0] * t->classes + k] +
                                (int32_t)t->last[context[1] * t->classes + k];
            t->q[k] = (int8_t)(sum > INT8_MAX ? INT8_MAX : sum < INT8_MIN ? INT8_MIN : sum);
        }
        const esp_err_t err =
            ai_engine_command_ctc_log_probs(t->q, t->exponent, t->classes, 1, rows + j * t->classes);
        if (err != ESP_OK) { return err; }
    }
    return ESP_OK;
}

bool parity_command_rnnt(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t frames, n_frames, exponent, first, last, units, n_variants, n_units, limits, beam, scores,
        decision;
    float beam_nats = 0.0f;
    if (!parity_tensor(buf, len, "frames", &frames) || !parity_tensor(buf, len, "n_frames", &n_frames) ||
        !parity_tensor(buf, len, "beam", &beam) || beam.dims[0] != 1 ||
        !parity_floats(&beam, &beam_nats, 1) || !parity_tensor(buf, len, "exponent", &exponent) ||
        !parity_tensor(buf, len, "first", &first) || !parity_tensor(buf, len, "last", &last) ||
        !parity_tensor(buf, len, "units", &units) || !parity_tensor(buf, len, "n_variants", &n_variants) ||
        !parity_tensor(buf, len, "n_units", &n_units) || !parity_tensor(buf, len, "limits", &limits) ||
        !parity_tensor(buf, len, "scores", &scores) || !parity_tensor(buf, len, "decision", &decision)) {
        return false;
    }
    const size_t windows = frames.dims[0], longest = frames.dims[1], classes = frames.dims[2];
    const size_t commands = units.dims[0], table = (classes + 1) * classes;
    if (n_frames.dims[0] != windows || exponent.dims[0] != windows || first.dims[0] != classes + 1 ||
        first.dims[1] != classes || last.dims[0] != classes + 1 || last.dims[1] != classes ||
        limits.dims[0] != windows || limits.dims[1] != LIMIT_COUNT || scores.dims[0] != windows ||
        scores.dims[1] != commands || decision.dims[0] != windows ||
        decision.dims[1] != PARITY_DECISION_COUNT) {
        return false;
    }
    float *raw = malloc(windows * longest * classes * sizeof(float));
    float *lengths = malloc(windows * sizeof(float));
    float *steps = malloc(windows * sizeof(float));
    float *pull_first = malloc(table * sizeof(float));
    float *pull_last = malloc(table * sizeof(float));
    float *limit = malloc(windows * LIMIT_COUNT * sizeof(float));
    float *want_scores = malloc(windows * commands * sizeof(float));
    float *want = malloc(windows * PARITY_DECISION_COUNT * sizeof(float));
    float *got_scores = malloc(windows * commands * sizeof(float));
    float *got = malloc(windows * PARITY_DECISION_COUNT * sizeof(float));
    int8_t *q = malloc(classes);
    ai_engine_lexicon_t *lex = calloc(1, sizeof(*lex));
    uint8_t *ids = malloc(units.dims[0] * units.dims[1] * units.dims[2]);
    void *tree = malloc(ai_engine_command_rnnt_tree_bytes());
    void *work = malloc(ai_engine_command_rnnt_work_bytes(classes));
    bool ok = raw != NULL && lengths != NULL && steps != NULL && pull_first != NULL && pull_last != NULL &&
              limit != NULL && want_scores != NULL && want != NULL && got_scores != NULL && got != NULL &&
              q != NULL && lex != NULL && ids != NULL && tree != NULL && work != NULL &&
              parity_floats(&frames, raw, windows * longest * classes) &&
              parity_floats(&n_frames, lengths, windows) && parity_floats(&exponent, steps, windows) &&
              parity_floats(&first, pull_first, table) && parity_floats(&last, pull_last, table) &&
              parity_floats(&limits, limit, windows * LIMIT_COUNT) &&
              parity_floats(&scores, want_scores, windows * commands) &&
              parity_floats(&decision, want, windows * PARITY_DECISION_COUNT) &&
              parity_lexicon_of(&units, &n_variants, &n_units, lex, ids) &&
              ai_engine_command_rnnt_build(lex, tree, ai_engine_command_rnnt_tree_bytes()) == ESP_OK;
    for (size_t w = 0; ok && w < windows; w++) {
        table_t t = {raw + w * longest * classes, pull_first, pull_last, classes, (int)steps[w], q};
        const float *l = limit + w * LIMIT_COUNT;
        ai_engine_command_result_t d;
        ok = ai_engine_command_rnnt_decide(lex, tree, classes, (size_t)lengths[w], (uint8_t)classes,
                                           beam_nats, table_rows, &t, (uint16_t)l[LIMIT_REJECT],
                                           (uint16_t)l[LIMIT_MARGIN], work, got_scores + w * commands,
                                           &d) == ESP_OK;
        parity_decision_row(&d, got + w * PARITY_DECISION_COUNT);
    }
    if (ok) {
        parity_mark_unreached(want_scores, windows * commands);
        parity_mark_unreached(got_scores, windows * commands);
        parity_report("command_rnnt", case_name, "scores", want_scores, got_scores, windows * commands);
        parity_report("command_rnnt", case_name, "decision", want, got, windows * PARITY_DECISION_COUNT);
    }
    free(raw);
    free(lengths);
    free(steps);
    free(pull_first);
    free(pull_last);
    free(limit);
    free(want_scores);
    free(want);
    free(got_scores);
    free(got);
    free(q);
    free(lex);
    free(ids);
    free(tree);
    free(work);
    return ok;
}
