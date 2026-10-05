/** The ctc decision after the encoder, pure C and mirrored 1:1 by srpipe.tasks.command.ctc.postproc.ctc_score
 *  (KEHOACH 3.12); contracts/golden/command_ctc/ holds it to the mirror.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "ai_engine.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define AI_ENGINE_COMMAND_CTC_REJECTED (-1)
#define AI_ENGINE_COMMAND_CTC_UNITS_MAX 48    // a variant's units; its forward pass keeps 2 n + 1 states
#define AI_ENGINE_COMMAND_CTC_CLASSES_MAX 64  // a frame's classes, one exp kept on the stack each
#define AI_ENGINE_COMMAND_CTC_FEATURES_MAX 96 // a hop's features, normalised on the stack

/** Log-probabilities of int8 logits worth logits * 2^exponent, frame after frame of n_classes, as
 *  ctc_score.py's frame_log_probs: largest off, exp summed over an exponent of its own, log in double.
 *  @ctx any | non-blocking | caller owns logits and log_probs, both n_frames x n_classes
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG no class, or more than AI_ENGINE_COMMAND_CTC_CLASSES_MAX
 */
esp_err_t ai_engine_command_ctc_log_probs(const int8_t *logits, int exponent, size_t n_classes,
                                          size_t n_frames, float *log_probs);

/** e^x as ctc_score.py's exp_wide gives it, by the same float32 steps: a mantissa, 0 or in [1, 2), and its
 *  exponent; 0 below the exp floor and for NaN.
 *  @ctx any | non-blocking
 */
float ai_engine_command_ctc_exp(float x, int32_t *exponent);

/** x in thousandths, rounded half to even, held within the uint16 fields of ai_engine_command_result_t.
 *  @ctx any | non-blocking
 */
uint16_t ai_engine_command_ctc_milli(float x);

/** Bytes of the work area of a window of up to n_frames: every forward pass, then the probabilities.
 *  @ctx any | non-blocking
 */
size_t ai_engine_command_ctc_work_bytes(size_t n_classes, size_t n_frames);

/** Lay out every variant's labels in work for the forward passes, as the next windows score them.
 *  @ctx any | non-blocking | caller owns lexicon, read and not kept, and work, 4-byte aligned
 *  @param n_frames the most frames a window holds, as ai_engine_command_ctc_work_bytes sized work
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE a variant longer than the units max
 */
esp_err_t ai_engine_command_ctc_prepare(const ai_engine_lexicon_t *lexicon, size_t n_classes, size_t n_frames,
                                        void *work);

/** Whether work is laid out for exactly lexicon's commands, variants and units.
 *  @ctx any | non-blocking | caller owns lexicon and work
 */
bool ai_engine_command_ctc_prepared_for(const ai_engine_lexicon_t *lexicon, const void *work);

/** Start a window on the prepared commands: no frame yet.
 *  @ctx any | non-blocking | caller owns work, prepared
 */
void ai_engine_command_ctc_begin(void *work);

/** Carry every forward pass and the free loop over the window's next n_frames, as one pass would.
 *  @ctx any | non-blocking | caller owns log_probs (n_frames x n_classes, read and not kept) and work
 *  @ret ESP_OK | ESP_ERR_INVALID_SIZE past the frames prepare sized work for |
 *       ESP_ERR_INVALID_STATE no prepare
 */
esp_err_t ai_engine_command_ctc_frames(const float *log_probs, size_t n_frames, void *work);

/** Take the command whose best variant scores highest over the frames so far, or reject (KEHOACH 3.12).
 *  @ctx any | non-blocking | caller owns lexicon, the prepared one, work and scores (n_commands or NULL)
 *  @param per_frames every score's divisor, a window_s window's frames; reject, margin in thousandths of it
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_STATE no prepare
 */
esp_err_t ai_engine_command_ctc_finish(const ai_engine_lexicon_t *lexicon, size_t per_frames, uint16_t reject,
                                       uint16_t margin, const void *work, float *scores,
                                       ai_engine_command_result_t *out);

/** A whole window at once: prepare for frames_cap, begin, every frame, finish; work stays prepared after.
 *  @ctx any | non-blocking, a few ms | caller owns log_probs (n_frames x n_classes), work and scores
 *  @param frames_cap frames ..._work_bytes sized work for, at least n_frames; per_frames the divisor
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE a variant past the units max or n_frames past cap
 */
esp_err_t ai_engine_command_ctc_decide(const float *log_probs, size_t n_classes, size_t n_frames,
                                       size_t frames_cap, const ai_engine_lexicon_t *lexicon,
                                       size_t per_frames, uint16_t reject, uint16_t margin, void *work,
                                       float *scores, ai_engine_command_result_t *out);

#ifdef __cplusplus
}
#endif
