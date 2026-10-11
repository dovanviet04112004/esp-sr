/** The speaker scoring after b0, pure C and mirrored 1:1 by srpipe.tasks.speaker.postproc.score
 * (KEHOACH 3.17, ADR-0023); contracts/golden/speaker/ holds it to the mirror.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define AI_ENGINE_SPEAKER_SCORE_LIMIT 1000 // permille of a cosine
#define AI_ENGINE_SPEAKER_Z_LIMIT 32767    // thousandths of the AS-norm score held in int16

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

/** A vector's mean and deviation over the top of rows cohort rows of dims: its dot products, the top highest,
 * a tie to the earlier row, float32 sums over them in row order, the deviation 1 when it is 0.
 *  @ctx any | non-blocking | caller owns every pointer; scores and order hold rows each; 1 <= top <= rows
 */
void ai_engine_speaker_norm_of(const float *vector, const float *cohort, size_t rows, size_t dims, size_t top,
                               float *scores, uint16_t *order, float *mean, float *std);

/** The highest AS-norm score of unit over n voiceprints, rows of dims with their means and deviations, unit's
 * own beside them, in thousandths within the limit; index the row that gave it, the lowest on a tie.
 *  @ctx any | non-blocking | caller owns every pointer
 *  @ret the score; n 0 gives -AI_ENGINE_SPEAKER_Z_LIMIT, index 0, cosine -AI_ENGINE_SPEAKER_SCORE_LIMIT
 */
int16_t ai_engine_speaker_best_as(const float *unit, const float *prints, const float *means,
                                  const float *stds, size_t n, size_t dims, float unit_mean, float unit_std,
                                  size_t *index, int16_t *cosine_permille);

/** Whether a score reaches the deployed threshold, both in thousandths of the AS-norm score.
 *  @ctx any | non-blocking
 */
bool ai_engine_speaker_accepts(int16_t milli_z, int16_t threshold_milli_z);

#ifdef __cplusplus
}
#endif
