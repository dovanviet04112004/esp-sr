/** SwiftF0's decoding after its network, pure C and mirrored 1:1 by
 * srpipe.tasks.pitch.postproc.decode.decode_int8 (KEHOACH 3.11 step 3.3); contracts/golden/pitch_decode/
 * holds it to the mirror.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define AI_ENGINE_PITCH_BINS 95        // log-spaced steps of the network's pitch logits
#define AI_ENGINE_PITCH_INT8_STEPS 256 // values of an int8

typedef struct {
    float steps_log[AI_ENGINE_PITCH_BINS];      // each step's ln Hz
    float weights[AI_ENGINE_PITCH_INT8_STEPS];  // e^(-k 2^e), k int8 steps under the most
    float sigmoids[AI_ENGINE_PITCH_INT8_STEPS]; // of each confidence logit, at q + 128
    int lo;                                     // the first step within the range
    int hi;                                     // and the last
} ai_engine_pitch_decoder_t;

/** Build the tables in double, each rounded to float32, for the steps within [f_min_hz, f_max_hz] and the
 * logits' and the confidence's exponents.
 *  @ctx any | non-blocking | caller owns d
 *  @ret false when no step lies within the range
 */
bool ai_engine_pitch_decoder_init(ai_engine_pitch_decoder_t *d, float f_min_hz, float f_max_hz,
                                  int logit_exponent, int sure_exponent);

/** One frame's log F0 and confidence from its AI_ENGINE_PITCH_BINS int8 pitch logits, stride apart, and its
 * int8 confidence logit; the confidence is 0 when the most likely of all steps lies outside the range.
 *  @ctx any | non-blocking | caller owns every pointer
 */
void ai_engine_pitch_decode(const ai_engine_pitch_decoder_t *d, const int8_t *logits, size_t stride,
                            int8_t sure, float *log_f0, float *confidence);

#ifdef __cplusplus
}
#endif
