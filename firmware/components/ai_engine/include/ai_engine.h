/** Every learned model: ns, wake, command, synth, run on esp-dl from the active model slot (KEHOACH 4.5.4).
 *  Models are process-wide singletons, loaded once at boot; each step belongs to one task (KEHOACH 5.2).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "dsp_afe/ns.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define AI_ENGINE_VARIANTS_MAX 4
#define AI_ENGINE_COMMANDS_MAX 64

typedef enum {
    AI_ENGINE_MODEL_NS = 0,
    AI_ENGINE_MODEL_WAKE,
    AI_ENGINE_MODEL_COMMAND,
    AI_ENGINE_MODEL_SYNTH,
    AI_ENGINE_MODEL_COUNT,
} ai_engine_model_t;

typedef struct {
    uint8_t n_units;
    const uint8_t *units; // ids of lang_vi, owned by the caller
} ai_engine_seq_t;

typedef struct {
    uint8_t n_commands;
    uint8_t n_variants[AI_ENGINE_COMMANDS_MAX];
    ai_engine_seq_t variants[AI_ENGINE_COMMANDS_MAX][AI_ENGINE_VARIANTS_MAX];
} ai_engine_lexicon_t;

typedef struct {
    int16_t command; // index into the lexicon, -1 when rejected
    uint16_t score_permille;
    uint16_t margin_permille;   // best over second best
    uint16_t free_gap_permille; // free unit loop over best
} ai_engine_command_result_t;

/** Map the model slot, verify sha256 and grid hash, and load every model the image holds.
 *  @ctx task | blocking, reads flash for seconds | once at boot; no step may run until it returns
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND no image | ESP_ERR_INVALID_VERSION grid hash differs | ESP_ERR_INVALID_CRC
 */
esp_err_t ai_engine_load(uint8_t slot);

/** Whether the loaded image carries this model.
 *  @ctx any | non-blocking
 */
bool ai_engine_has(ai_engine_model_t model);

/** The learned ns as a dsp_afe slot implementation; NULL when the image has no ns.
 *  @ctx any | non-blocking | its process runs in sach_task on core 1
 */
const dsp_afe_ns_ops_t *ai_engine_ns_ops(void);

/** Feed one frame of normalised-inside log-mel features; score is the smoothed wake probability.
 *  @ctx nhan_task | non-blocking | features are the raw log-mel of dsp_spec_mel_log
 */
esp_err_t ai_engine_wake_step(const float *log_mel, uint16_t *score_permille);

/** Clear the streaming state of wake, as after a gap in the frame sequence.
 *  @ctx nhan_task | non-blocking
 */
void ai_engine_wake_reset(void);

/** Start a command window; unit posteriors accumulate until ai_engine_command_score.
 *  @ctx nhan_task | non-blocking
 */
esp_err_t ai_engine_command_begin(void);

/** Feed one frame of features into the open command window.
 *  @ctx nhan_task | non-blocking
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no window open | ESP_ERR_NO_MEM window longer than 3 s
 */
esp_err_t ai_engine_command_step(const float *log_mel);

/** Close the window and score every variant of every command by constrained CTC (KEHOACH 3.12).
 *  @ctx nhan_task | non-blocking, a few ms | lexicon is read, not kept
 */
esp_err_t ai_engine_command_score(const ai_engine_lexicon_t *lexicon, ai_engine_command_result_t *out);

/** Render units to 16 kHz PCM; n_samples gets the count written.
 *  @ctx noi_task | non-blocking, runs slower than real time is allowed | caller owns pcm
 *  @ret ESP_OK | ESP_ERR_NOT_SUPPORTED no synth in the image | ESP_ERR_INVALID_SIZE pcm too short
 */
esp_err_t ai_engine_synth_render(const uint8_t *units, size_t n_units, int16_t *pcm, size_t cap,
                                 size_t *n_samples);

#ifdef __cplusplus
}
#endif
