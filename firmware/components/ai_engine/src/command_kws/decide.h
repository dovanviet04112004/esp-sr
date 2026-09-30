/** The kws decision after the DS-CNN, pure C and mirrored 1:1 by srpipe.tasks.command.kws.postproc.decide
 *  (KEHOACH 3.12); contracts/golden/command_kws/ holds it to the mirror.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#define AI_ENGINE_COMMAND_KWS_REJECTED (-1)
#define AI_ENGINE_COMMAND_KWS_EXTRA_CLASSES 2 // other, then silence, after the learned commands

typedef struct {
    int16_t command;          // learned command index, or AI_ENGINE_COMMAND_KWS_REJECTED
    uint16_t score_permille;  // winner's probability
    uint16_t margin_permille; // winner over the second class
    uint16_t rest_permille;   // other plus silence
} ai_engine_command_kws_decision_t;

/** Softmax n_commands + 2 logits into probs, then take the winner unless other or silence wins, its
 * probability is under reject_permille, or its lead over the second class is under margin_permille.
 *  @ctx any | non-blocking | caller owns probs, n_commands + 2 floats
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG
 */
esp_err_t ai_engine_command_kws_decide(const float *logits, size_t n_commands, uint16_t reject_permille,
                                       uint16_t margin_permille, float *probs,
                                       ai_engine_command_kws_decision_t *out);
