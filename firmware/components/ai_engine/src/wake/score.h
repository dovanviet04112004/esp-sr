/** Wake's score after its network, pure C and mirrored 1:1 by srpipe.tasks.wake.postproc.smooth.chip_scores
 * (KEHOACH 3.11): each int8 logit to permille through a table, then a trailing mean in integers;
 * contracts/golden/wake_score/ holds it to the mirror.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define AI_ENGINE_WAKE_INT8_STEPS 256 // values of an int8, -128 first

typedef struct {
    uint16_t permille[AI_ENGINE_WAKE_INT8_STEPS]; // of each logit, at q + 128
    uint16_t *recent;                             // the last hops permille, a ring the caller owns
    size_t hops;
    size_t next; // the ring's slot the next permille takes
    size_t seen; // permille the ring holds, up to hops
    uint32_t sum;
} ai_engine_wake_scorer_t;

/** Build the table in double for the logits' exponent, 1000 / (1 + e^-x) rounded half up, and start empty.
 *  @ctx any | non-blocking | caller owns s and recent, hops entries
 */
void ai_engine_wake_scorer_init(ai_engine_wake_scorer_t *s, int exponent, uint16_t *recent, size_t hops);

/** Forget every permille pushed: the next mean is over what comes after.
 *  @ctx any | non-blocking
 */
void ai_engine_wake_scorer_reset(ai_engine_wake_scorer_t *s);

/** The mean permille of this logit's and the last hops - 1 pushed, rounded half up; over fewer while fewer
 * came.
 *  @ctx any | non-blocking
 */
uint16_t ai_engine_wake_score(ai_engine_wake_scorer_t *s, int8_t logit);

#ifdef __cplusplus
}
#endif
