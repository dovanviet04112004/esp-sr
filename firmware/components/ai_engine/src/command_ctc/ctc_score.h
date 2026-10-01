/** The ctc decision after the encoder, pure C and mirrored 1:1 by srpipe.tasks.command.ctc.postproc.ctc_score
 *  (KEHOACH 3.12); contracts/golden/command_ctc/ holds it to the mirror.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "ai_engine.h"
#include "esp_err.h"

#define AI_ENGINE_COMMAND_CTC_REJECTED (-1)
#define AI_ENGINE_COMMAND_CTC_UNITS_MAX 48 // a variant's units; its forward pass keeps 2 n + 1 states

/** Take the command whose best variant scores highest by the CTC forward pass, unless rejected
 * (KEHOACH 3.12).
 *  @ctx any | non-blocking, a few ms | caller owns log_probs, n_frames x n_classes frame by frame, and scores
 *  @param reject, margin in thousandths of a nat a frame; scores n_commands floats or NULL
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE a variant longer than the units max
 */
esp_err_t ai_engine_command_ctc_decide(const float *log_probs, size_t n_classes, size_t n_frames,
                                       const ai_engine_lexicon_t *lexicon, uint16_t reject, uint16_t margin,
                                       float *scores, ai_engine_command_result_t *out);
