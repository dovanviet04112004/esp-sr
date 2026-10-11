#include <stdlib.h>

#include "parity.h"
#include "speaker/score.h"

enum { DECISION_SCORE = 0, DECISION_ACCEPTED, DECISION_FIELDS };

bool parity_speaker(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t enrol, tests, exponent, threshold, units, voiceprint, decision;
    if (!parity_tensor(buf, len, "enrol_raw", &enrol) || enrol.dtype != GOLD_I8 ||
        !parity_tensor(buf, len, "test_raw", &tests) || tests.dtype != GOLD_I8 ||
        !parity_tensor(buf, len, "exponent", &exponent) ||
        !parity_tensor(buf, len, "threshold", &threshold) || !parity_tensor(buf, len, "units", &units) ||
        !parity_tensor(buf, len, "voiceprint", &voiceprint) ||
        !parity_tensor(buf, len, "decision", &decision)) {
        return false;
    }
    const size_t dims = enrol.dims[1];
    const size_t n_enrol = enrol.dims[0];
    const size_t n_tests = tests.dims[0];
    if (tests.dims[1] != dims || decision.dims[0] != n_tests || decision.dims[1] != DECISION_FIELDS) {
        return false;
    }
    const int e = *(const int32_t *)exponent.data;
    const int16_t threshold_permille = (int16_t)(*(const int32_t *)threshold.data);
    float *enrol_units = malloc(n_enrol * dims * sizeof(float));
    float *got_units = malloc(n_tests * dims * sizeof(float));
    float *got_print = malloc(dims * sizeof(float));
    float *got_decision = malloc(n_tests * DECISION_FIELDS * sizeof(float));
    float *want_decision = malloc(n_tests * DECISION_FIELDS * sizeof(float));
    bool ok = enrol_units != NULL && got_units != NULL && got_print != NULL && got_decision != NULL &&
              want_decision != NULL && parity_floats(&decision, want_decision, n_tests * DECISION_FIELDS);
    if (ok) {
        const int8_t *raw = enrol.data;
        for (size_t k = 0; k < n_enrol; k++) {
            ai_engine_speaker_unit(raw + k * dims, dims, e, enrol_units + k * dims);
        }
        ai_engine_speaker_fold(enrol_units, n_enrol, dims, got_print);
        raw = tests.data;
        for (size_t t = 0; t < n_tests; t++) {
            float *u = got_units + t * dims;
            ai_engine_speaker_unit(raw + t * dims, dims, e, u);
            const int16_t score = ai_engine_speaker_score_permille(u, got_print, dims);
            got_decision[t * DECISION_FIELDS + DECISION_SCORE] = score;
            got_decision[t * DECISION_FIELDS + DECISION_ACCEPTED] =
                ai_engine_speaker_accepts(score, threshold_permille) ? 1.0f : 0.0f;
        }
        parity_report("speaker", case_name, "units", units.data, got_units, n_tests * dims);
        parity_report("speaker", case_name, "voiceprint", voiceprint.data, got_print, dims);
        parity_report("speaker", case_name, "decision", want_decision, got_decision,
                      n_tests * DECISION_FIELDS);
    }
    free(enrol_units);
    free(got_units);
    free(got_print);
    free(got_decision);
    free(want_decision);
    return ok;
}
