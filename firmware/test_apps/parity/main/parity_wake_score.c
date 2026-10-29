#include <stdint.h>
#include <stdlib.h>

#include "parity.h"
#include "wake/score.h"

enum { PARAM_EXPONENT = 0, PARAM_HOPS, PARAMS };

bool parity_wake_score(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t logits, params, score;
    if (!parity_tensor(buf, len, "logits", &logits) || logits.dtype != GOLD_I8 ||
        !parity_tensor(buf, len, "params", &params) || !parity_tensor(buf, len, "score", &score)) {
        return false;
    }
    const size_t hops = logits.dims[0];
    float param[PARAMS];
    if (score.dims[0] != hops || !parity_floats(&params, param, PARAMS) || param[PARAM_HOPS] < 1) {
        return false;
    }
    static ai_engine_wake_scorer_t scorer;
    uint16_t *recent = malloc((size_t)param[PARAM_HOPS] * sizeof(uint16_t));
    float *got = malloc(hops * sizeof(float));
    const bool ok = recent != NULL && got != NULL;
    if (ok) {
        ai_engine_wake_scorer_init(&scorer, (int)param[PARAM_EXPONENT], recent, (size_t)param[PARAM_HOPS]);
        const int8_t *q = logits.data;
        for (size_t t = 0; t < hops; t++) {
            got[t] = (float)ai_engine_wake_score(&scorer, q[t]);
        }
        parity_report("wake_score", case_name, "score", score.data, got, hops);
    }
    free(recent);
    free(got);
    return ok;
}
