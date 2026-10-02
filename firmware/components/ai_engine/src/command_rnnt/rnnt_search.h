/** The rnnt decision after the joiner, pure C and mirrored 1:1 by rnnt/postproc/rnnt_search.py of srpipe
 *  (KEHOACH 3.12, ADR-0016); contracts/golden/command_rnnt/ holds it to the mirror.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "ai_engine.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define AI_ENGINE_COMMAND_RNNT_CONTEXT 2         // the predictor's context classes, as rnnt.context
#define AI_ENGINE_COMMAND_RNNT_UNITS_MAX 48      // a variant's units at most
#define AI_ENGINE_COMMAND_RNNT_NODES_MAX 8192    // prefix tree nodes of every variant and part
#define AI_ENGINE_COMMAND_RNNT_CONTEXTS_MAX 2048 // distinct predictor contexts of the tree
#define AI_ENGINE_COMMAND_RNNT_FREE_UNITS 4      // the greedy path's most units a frame, then blank

/** The log-probabilities of every class at frame for the predictor context, its last CONTEXT classes.
 *  @ctx the caller's | as the caller's model runs | context and log_probs belong to the decision
 */
typedef esp_err_t (*ai_engine_command_rnnt_log_probs_t)(void *ctx, size_t frame, const uint8_t *context,
                                                        float *log_probs);

/** Bytes of the area ai_engine_command_rnnt_build lays the command tree out in.
 *  @ctx any | non-blocking
 */
size_t ai_engine_command_rnnt_tree_bytes(void);

/** Lay out the prefix tree of every variant and part of every command breadth first, as command_tree does;
 *  a part is a run of whole syllables of a variant short of it, each node keeps its predictor context.
 *  @ctx any | non-blocking, a few ms | caller owns lexicon, read and not kept, and tree, 4-byte aligned
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_NO_MEM more nodes or contexts than the _MAX above
 */
esp_err_t ai_engine_command_rnnt_build(const ai_engine_lexicon_t *lexicon, void *tree, size_t bytes);

/** Bytes of the work area a decision over n_classes classes takes.
 *  @ctx any | non-blocking
 */
size_t ai_engine_command_rnnt_work_bytes(size_t n_classes);

/** Decide a window: every variant and part scored exactly over the tree, then the ctc track's rules.
 *  @ctx any | blocking while log_probs runs | caller owns lexicon, tree, work (4-byte aligned), scores
 *  @param pad the predictor's id ahead of the leading blank; scores n_commands floats or NULL
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE | log_probs' error
 */
esp_err_t ai_engine_command_rnnt_decide(const ai_engine_lexicon_t *lexicon, const void *tree,
                                        size_t n_classes, size_t n_frames, uint8_t pad,
                                        ai_engine_command_rnnt_log_probs_t log_probs, void *ctx,
                                        uint16_t reject, uint16_t margin, void *work, float *scores,
                                        ai_engine_command_result_t *out);

#ifdef __cplusplus
}
#endif
