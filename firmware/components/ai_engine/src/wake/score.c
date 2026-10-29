#include "wake/score.h"

#include <math.h>
#include <string.h>

#define PERMILLE 1000.0
#define INT8_MIN_VALUE (-128)

void ai_engine_wake_scorer_init(ai_engine_wake_scorer_t *s, int exponent, uint16_t *recent, size_t hops)
{
    for (int k = 0; k < AI_ENGINE_WAKE_INT8_STEPS; k++) {
        const double x = ldexp((double)(k + INT8_MIN_VALUE), exponent);
        s->permille[k] = (uint16_t)floor(PERMILLE / (1.0 + exp(-x)) + 0.5);
    }
    s->recent = recent;
    s->hops = hops;
    ai_engine_wake_scorer_reset(s);
}

void ai_engine_wake_scorer_reset(ai_engine_wake_scorer_t *s)
{
    memset(s->recent, 0, s->hops * sizeof(s->recent[0]));
    s->next = 0;
    s->seen = 0;
    s->sum = 0;
}

uint16_t ai_engine_wake_score(ai_engine_wake_scorer_t *s, int8_t logit)
{
    if (s->seen == s->hops) {
        s->sum -= s->recent[s->next];
    } else {
        s->seen++;
    }
    s->recent[s->next] = s->permille[logit - INT8_MIN_VALUE];
    s->sum += s->recent[s->next];
    s->next = (s->next + 1) % s->hops;
    return (uint16_t)((s->sum + s->seen / 2) / s->seen);
}
