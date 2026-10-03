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
#define AI_ENGINE_COMMAND_RNNT_BEAM_NATS 15.0f   // a node this far behind a frame's best is dropped

/** The log-probabilities of every class at frame for n predictor contexts, each its last CONTEXT classes.
 *  @ctx the caller's | as the caller's model runs | contexts (n x CONTEXT) and rows (n x classes) are the
 * search's
 */
typedef esp_err_t (*ai_engine_command_rnnt_rows_t)(void *ctx, size_t frame, const uint8_t *contexts, size_t n,
                                                   float *rows);

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

/** Bytes of the work area a search over n_classes classes takes: its state between frames and one frame's
 * rows.
 *  @ctx any | non-blocking
 */
size_t ai_engine_command_rnnt_work_bytes(size_t n_classes);

/** Start a window's search over tree: everything at the root, no frame yet.
 *  @ctx any | non-blocking | caller owns tree and work (4-byte aligned) for the whole window
 *  @param pad the predictor's id ahead of the leading blank; beam_nats nodes this far behind a frame's best
 * drop
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG
 */
esp_err_t ai_engine_command_rnnt_begin(const void *tree, size_t n_classes, uint8_t pad, float beam_nats,
                                       void *work);

/** Search the next frame as frame does: far-behind nodes dropped, rows asked depth by depth, then the free
 * path.
 *  @ctx any | blocking while rows runs | caller owns tree and work, as begun
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no begin | ESP_ERR_INVALID_SIZE a unit past the classes | rows' error
 */
esp_err_t ai_engine_command_rnnt_frame(const void *tree, ai_engine_command_rnnt_rows_t rows, void *ctx,
                                       void *work);

/** Decide on the frames searched so far, by the ctc track's rules.
 *  @ctx any | non-blocking | caller owns lexicon, the tree's, tree and work; scores n_commands floats or NULL
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_STATE no begin
 */
esp_err_t ai_engine_command_rnnt_finish(const ai_engine_lexicon_t *lexicon, const void *tree,
                                        const void *work, uint16_t reject, uint16_t margin, float *scores,
                                        ai_engine_command_result_t *out);

/** Decide a whole window at once: begin, every frame, finish.
 *  @ctx any | blocking while rows runs | caller owns lexicon, tree, work (4-byte aligned), scores
 *  @param pad the predictor's id ahead of the leading blank; scores n_commands floats or NULL
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE | rows' error
 */
esp_err_t ai_engine_command_rnnt_decide(const ai_engine_lexicon_t *lexicon, const void *tree,
                                        size_t n_classes, size_t n_frames, uint8_t pad, float beam_nats,
                                        ai_engine_command_rnnt_rows_t rows, void *ctx, uint16_t reject,
                                        uint16_t margin, void *work, float *scores,
                                        ai_engine_command_result_t *out);

#ifdef __cplusplus
}
#endif
