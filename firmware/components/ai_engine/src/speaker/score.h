/** The speaker scoring after b0, pure C and mirrored 1:1 by srpipe.tasks.speaker.postproc.score
 * (KEHOACH 3.17); contracts/golden/speaker/ holds it to the mirror.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define AI_ENGINE_SPEAKER_SCORE_LIMIT 1000 // permille of a cosine

/** The network's int8 embedding times 2^exponent at unit length, float32; zeros stay zeros.
 *  @ctx any | non-blocking | caller owns raw and unit, dims each
 */
void ai_engine_speaker_unit(const int8_t *raw, size_t dims, int exponent, float *unit);

/** The voiceprint: n unit embeddings, rows of dims, summed in their order, at unit length.
 *  @ctx any | non-blocking | caller owns units and voiceprint
 */
void ai_engine_speaker_fold(const float *units, size_t n, size_t dims, float *voiceprint);

/** Permille of the dot product of two unit vectors, rounded half to even, within the score limit.
 *  @ctx any | non-blocking
 */
int16_t ai_engine_speaker_score_permille(const float *unit, const float *voiceprint, size_t dims);

/** The highest score of unit over n voiceprints, rows of dims, and in index the row that gave it, the lowest
 * on a tie; n 0 gives -AI_ENGINE_SPEAKER_SCORE_LIMIT and index 0.
 *  @ctx any | non-blocking | caller owns unit, prints and index
 */
int16_t ai_engine_speaker_best_permille(const float *unit, const float *prints, size_t n, size_t dims,
                                        size_t *index);

/** Whether a score reaches the deployed threshold.
 *  @ctx any | non-blocking
 */
bool ai_engine_speaker_accepts(int16_t score_permille, int16_t threshold_permille);

#ifdef __cplusplus
}
#endif
