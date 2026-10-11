#include <stdint.h>
#include <stdlib.h>

#include "parity.h"
#include "speaker/score.h"

enum { DECISION_SCORE = 0, DECISION_ACCEPTED, DECISION_INDEX, DECISION_COSINE, DECISION_FIELDS };
enum { NORM_MEAN = 0, NORM_STD, NORM_FIELDS };

bool parity_speaker(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t enrol, tests, exponent, threshold, people, cohort, top, units, voiceprint, norms,
        unit_norms, decision;
    if (!parity_tensor(buf, len, "enrol_raw", &enrol) || enrol.dtype != GOLD_I8 ||
        !parity_tensor(buf, len, "test_raw", &tests) || tests.dtype != GOLD_I8 ||
        !parity_tensor(buf, len, "exponent", &exponent) ||
        !parity_tensor(buf, len, "threshold", &threshold) || !parity_tensor(buf, len, "people", &people) ||
        !parity_tensor(buf, len, "cohort", &cohort) || cohort.dtype != GOLD_F32 ||
        !parity_tensor(buf, len, "top", &top) || !parity_tensor(buf, len, "units", &units) ||
        !parity_tensor(buf, len, "voiceprint", &voiceprint) || !parity_tensor(buf, len, "norms", &norms) ||
        !parity_tensor(buf, len, "unit_norms", &unit_norms) ||
        !parity_tensor(buf, len, "decision", &decision)) {
        return false;
    }
    const size_t dims = enrol.dims[1];
    const size_t n_people = (size_t)(*(const int32_t *)people.data);
    const size_t n_tests = tests.dims[0];
    const size_t rows = cohort.dims[0];
    const size_t n_top = (size_t)(*(const int32_t *)top.data);
    if (tests.dims[1] != dims || cohort.dims[1] != dims || rows == 0 || rows > UINT16_MAX || n_top == 0 ||
        n_top > rows || n_people == 0 || enrol.dims[0] % n_people != 0 || decision.dims[0] != n_tests ||
        decision.dims[1] != DECISION_FIELDS) {
        return false;
    }
    const size_t per_person = enrol.dims[0] / n_people;
    const int e = *(const int32_t *)exponent.data;
    const int16_t threshold_milli_z = (int16_t)(*(const int32_t *)threshold.data);
    float *enrol_units = malloc(per_person * dims * sizeof(float));
    float *got_units = malloc(n_tests * dims * sizeof(float));
    float *got_prints = malloc(n_people * dims * sizeof(float));
    float *got_norms = malloc(n_people * NORM_FIELDS * sizeof(float));
    float *means = malloc(n_people * sizeof(float));
    float *stds = malloc(n_people * sizeof(float));
    float *got_unit_norms = malloc(n_tests * NORM_FIELDS * sizeof(float));
    float *scores = malloc(rows * sizeof(float));
    uint16_t *order = malloc(rows * sizeof(uint16_t));
    float *got_decision = malloc(n_tests * DECISION_FIELDS * sizeof(float));
    float *want_decision = malloc(n_tests * DECISION_FIELDS * sizeof(float));
    bool ok = enrol_units != NULL && got_units != NULL && got_prints != NULL && got_norms != NULL &&
              means != NULL && stds != NULL && got_unit_norms != NULL && scores != NULL && order != NULL &&
              got_decision != NULL && want_decision != NULL &&
              parity_floats(&decision, want_decision, n_tests * DECISION_FIELDS);
    if (ok) {
        const int8_t *raw = enrol.data;
        for (size_t p = 0; p < n_people; p++) {
            for (size_t k = 0; k < per_person; k++) {
                ai_engine_speaker_unit(raw + (p * per_person + k) * dims, dims, e, enrol_units + k * dims);
            }
            ai_engine_speaker_fold(enrol_units, per_person, dims, got_prints + p * dims);
            ai_engine_speaker_norm_of(got_prints + p * dims, cohort.data, rows, dims, n_top, scores, order,
                                      &means[p], &stds[p]);
            got_norms[p * NORM_FIELDS + NORM_MEAN] = means[p];
            got_norms[p * NORM_FIELDS + NORM_STD] = stds[p];
        }
        raw = tests.data;
        for (size_t t = 0; t < n_tests; t++) {
            float *u = got_units + t * dims;
            ai_engine_speaker_unit(raw + t * dims, dims, e, u);
            float *own = got_unit_norms + t * NORM_FIELDS;
            ai_engine_speaker_norm_of(u, cohort.data, rows, dims, n_top, scores, order, &own[NORM_MEAN],
                                      &own[NORM_STD]);
            size_t index = 0;
            int16_t cosine = 0;
            const int16_t score = ai_engine_speaker_best_as(u, got_prints, means, stds, n_people, dims,
                                                            own[NORM_MEAN], own[NORM_STD], &index, &cosine);
            got_decision[t * DECISION_FIELDS + DECISION_SCORE] = score;
            got_decision[t * DECISION_FIELDS + DECISION_ACCEPTED] =
                ai_engine_speaker_accepts(score, threshold_milli_z) ? 1.0f : 0.0f;
            got_decision[t * DECISION_FIELDS + DECISION_INDEX] = (float)index;
            got_decision[t * DECISION_FIELDS + DECISION_COSINE] = cosine;
        }
        parity_report("speaker", case_name, "units", units.data, got_units, n_tests * dims);
        parity_report("speaker", case_name, "voiceprint", voiceprint.data, got_prints, n_people * dims);
        parity_report("speaker", case_name, "norms", norms.data, got_norms, n_people * NORM_FIELDS);
        parity_report("speaker", case_name, "unit_norms", unit_norms.data, got_unit_norms,
                      n_tests * NORM_FIELDS);
        parity_report("speaker", case_name, "decision", want_decision, got_decision,
                      n_tests * DECISION_FIELDS);
    }
    free(enrol_units);
    free(got_units);
    free(got_prints);
    free(got_norms);
    free(means);
    free(stds);
    free(got_unit_norms);
    free(scores);
    free(order);
    free(got_decision);
    free(want_decision);
    return ok;
}
