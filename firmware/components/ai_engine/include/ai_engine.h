/** Every learned model: ns, wake, command, speaker, pitch, synth, run on esp-dl from the model slot
 * (KEHOACH 4.5.4). Models are process-wide singletons, loaded once at boot; each step belongs to one task
 * (KEHOACH 5.2).
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "dsp_afe/ns.h"
#include "dsp_spec/swift_front.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define AI_ENGINE_VARIANTS_MAX 4
#define AI_ENGINE_COMMANDS_MAX 64
#define AI_ENGINE_SPEAKER_DIMS_MAX 256 // the speaker embedding's dimensions
#define AI_ENGINE_SPEAKER_NOISE_BINS 4 // the last the quietest (ADR-0024)

typedef enum {
    AI_ENGINE_MODEL_NS = 0,
    AI_ENGINE_MODEL_WAKE,
    AI_ENGINE_MODEL_COMMAND,
    AI_ENGINE_MODEL_SYNTH,
    AI_ENGINE_MODEL_SPEAKER,
    AI_ENGINE_MODEL_PITCH,
    AI_ENGINE_MODEL_COUNT,
} ai_engine_model_t;

typedef struct {
    uint8_t n_units;
    const uint8_t *units; // ids of lang_vi, owned by the caller
} ai_engine_seq_t;

typedef struct {
    uint16_t n_commands;
    uint8_t n_variants[AI_ENGINE_COMMANDS_MAX];
    ai_engine_seq_t variants[AI_ENGINE_COMMANDS_MAX][AI_ENGINE_VARIANTS_MAX];
} ai_engine_lexicon_t;

typedef struct {
    int16_t command; // index into the lexicon, -1 when rejected
    uint16_t score_permille;
    uint16_t margin_permille;       // best over second best
    uint16_t free_gap_permille;     // free unit loop over best
    uint16_t syllable_gap_permille; // free loop over best's worst syllable
} ai_engine_command_result_t;

/** Map the model slot, verify sha256 and grid hash, copy every model into PSRAM, then unmap (KEHOACH 6.5).
 *  @ctx task | blocking, reads flash for seconds | once at boot; no step may run until it returns
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND no image | ESP_ERR_INVALID_VERSION grid hash differs | ESP_ERR_INVALID_CRC
 *       | ESP_ERR_NO_MEM PSRAM too small for weights and workspace
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

/** Feed one frame of raw log-mel; score is the smoothed wake probability of the frame ai_engine_wake_delay
 *  calls back, 0 until that many have come since load or reset (KEHOACH 3.11).
 *  @ctx nhan_task | non-blocking; the call that completes the graph's frames runs it, ~2.3 ms
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE the loaded image has no wake | ESP_ERR_INVALID_ARG
 */
esp_err_t ai_engine_wake_step(const float *log_mel, uint16_t *score_permille);

/** Frames the score of ai_engine_wake_step trails the frame fed; 0 when the image has no wake.
 *  @ctx any | non-blocking
 */
size_t ai_engine_wake_delay(void);

/** Clear the streaming state of wake, as after a gap in the frame sequence.
 *  @ctx nhan_task | non-blocking
 */
void ai_engine_wake_reset(void);

/** Take the commands the next windows are scored with, so each step scores its frames (KEHOACH 3.12).
 *  @ctx nhan_task | blocking: rnnt runs its predictor once a new context | no window open; lexicon read
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no command in the image, or a window open | ESP_ERR_INVALID_ARG |
 *       ESP_ERR_INVALID_SIZE a variant past the backend's units | ESP_ERR_NO_MEM a tree past its nodes
 */
esp_err_t ai_engine_command_prepare(const ai_engine_lexicon_t *lexicon);

/** Start a command window; unit posteriors accumulate until ai_engine_command_score.
 *  @ctx nhan_task | non-blocking
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE the loaded image has no command
 */
esp_err_t ai_engine_command_begin(void);

/** Feed one frame of features into the open command window.
 *  @ctx nhan_task | non-blocking
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no window open | ESP_ERR_NO_MEM window past GEN_LISTEN_WINDOW_HOPS
 */
esp_err_t ai_engine_command_step(const float *log_mel);

/** Close the window and decide it on lexicon: the prepared set finishes its scores, another rescans.
 *  @ctx nhan_task | non-blocking: the last chunk and the finish, a rescan the whole score | lexicon read
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no window open
 */
esp_err_t ai_engine_command_score(const ai_engine_lexicon_t *lexicon, ai_engine_command_result_t *out);

/** Close the open command window unscored, as for an utterance the listener drops (KEHOACH 5.4).
 *  @ctx nhan_task | non-blocking
 *  @ret ESP_OK, with a window open or none | ESP_ERR_INVALID_STATE the loaded image has no command
 */
esp_err_t ai_engine_command_abort(void);

/** Features a hop the loaded command model takes: its mel bands then pitch's three (ADR-0017).
 *  @ctx any | non-blocking
 *  @ret the count, or 0 without a command model or with a backend that does not read it
 */
size_t ai_engine_command_features(void);

/** Embed the last GEN_SPEAKER_WINDOW_SAMPLES of a command window: spk_fbank's features, one run, unit length
 * (KEHOACH 3.17).
 *  @ctx xac_task | blocking, ~1.5 s of core 0 (b3) | pcm read; unit gets ai_engine_speaker_dims() floats
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_STATE no speaker in the image
 */
esp_err_t ai_engine_speaker_embed(const int16_t *pcm, float *unit);

/** Dimensions of the speaker embedding; 0 without speaker.
 *  @ctx any | non-blocking
 */
size_t ai_engine_speaker_dims(void);

/** The voiceprint of n unit embeddings, rows of ai_engine_speaker_dims() floats: their sum at unit length.
 *  @ctx any | non-blocking | caller owns units and voiceprint
 */
void ai_engine_speaker_voiceprint(const float *units, size_t n, float *voiceprint);

/** A voiceprint's or an embedding's mean and deviation over the top rows of the image's cohort of strangers,
 * which ai_engine_speaker_best scores by (ADR-0023).
 *  @ctx task | blocking: reads the cohort in place from flash | one task, the scratch is shared with best
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_STATE no speaker in the image
 */
esp_err_t ai_engine_speaker_norm(const float *voiceprint, float *mean, float *std);

/** The highest AS-norm score of unit over n voiceprints, rows of ai_engine_speaker_dims() floats with the
 * means and deviations ai_engine_speaker_norm gave them, in thousandths; in index the row that gave it, the
 * lowest on a tie, in cosine_permille that row's cosine (KEHOACH 3.17, ADR-0023).
 *  @ctx task | blocking: reads the cohort for unit's own | the task that calls ai_engine_speaker_norm
 *  @ret the score; -32767 with index 0 and cosine -1000 when n is 0 or there is no speaker
 */
int16_t ai_engine_speaker_best(const float *unit, const float *prints, const float *means, const float *stds,
                               size_t n, size_t *index, int16_t *cosine_permille);

/** A command window's noise bin from its int16 samples summed squared over the lead ahead of its utterance
 * and over the utterance, against the image's edges (ADR-0024): 0 the noisiest; the quietest,
 * AI_ENGINE_SPEAKER_NOISE_BINS - 1, with no lead, a silent one, no utterance or no speaker.
 *  @ctx any | non-blocking
 */
uint8_t ai_engine_speaker_noise_bin(uint64_t lead_energy, uint32_t lead_samples, uint64_t speech_energy,
                                    uint32_t speech_samples);

/** Whether a best score over prints voiceprints, thousandths of the AS-norm score, reaches the image's
 * threshold for that many in that noise bin (KEHOACH 3.17, 6.3, ADR-0024).
 *  @ctx any | non-blocking
 *  @ret false with no voiceprint, more than APP_SPEAKERS_MAX, or a bin past the quietest
 */
bool ai_engine_speaker_passes(int16_t milli_z, size_t prints, uint8_t noise_bin);

/** The id of the loaded speaker entry, the first four bytes of its sha256, that a voiceprint belongs to.
 *  @ctx any | non-blocking
 *  @ret the id, or 0 without speaker
 */
uint32_t ai_engine_speaker_model_id(void);

/** Render units to 16 kHz PCM; n_samples gets the count written.
 *  @ctx noi_task | non-blocking, runs slower than real time is allowed | caller owns pcm
 *  @ret ESP_OK | ESP_ERR_NOT_SUPPORTED no synth in the image | ESP_ERR_INVALID_SIZE pcm too short
 */
esp_err_t ai_engine_synth_render(const uint8_t *units, size_t n_units, int16_t *pcm, size_t cap,
                                 size_t *n_samples);

/** Run the part of an ai_engine op a network's task hands over, waiting up to wait_ms for one (KEHOACH 5.1).
 *  @ctx phu_task, pinned to core 1 below thu and sach | blocks up to wait_ms | built with AI_ENGINE_CORES_TWO
 */
void ai_engine_helper_serve(uint32_t wait_ms);

/** SwiftF0's front end tables, pointers into the image's FRONT entry for dsp_spec_swift_front_init
 * (KEHOACH 3.11).
 *  @ctx task | non-blocking | valid while the image stays loaded
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no pitch in the image
 */
esp_err_t ai_engine_pitch_tables(dsp_spec_swift_front_tables_t tables[DSP_SPEC_SWIFT_FRONT_SPECTRA]);

/** Frames a call of ai_engine_pitch_run takes and gives; 0 without a pitch.
 *  @ctx any | non-blocking
 */
size_t ai_engine_pitch_frames(void);

/** Frames the decoded frames of a call trail the front frames it takes; 0 without a pitch.
 *  @ctx any | non-blocking
 */
size_t ai_engine_pitch_delay(void);

/** Run SwiftF0's network over the next front frames, ai_engine_pitch_frames() of DSP_SPEC_SWIFT_FRONT_SPECTRA
 * times BANDS floats each, and decode as many frames, the first ai_engine_pitch_delay() behind the first
 * taken: ln Hz and confidence, not yet gated by silence.
 *  @ctx nhan_task | ~28 ms of core 0 for 16 frames | caller owns every pointer
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_STATE no pitch in the image | esp-dl's error
 */
esp_err_t ai_engine_pitch_run(const float *front, float *log_f0, float *confidence);

/** Forget every frame the network keeps, as at the start of a stream.
 *  @ctx nhan_task | non-blocking
 */
void ai_engine_pitch_reset(void);

typedef struct {
    int16_t x1; // left edge, pixels of the picture run took
    int16_t y1;
    int16_t x2;
    int16_t y2;
    uint16_t cls;            // index for ai_engine_detect_name
    uint16_t score_permille; // sigmoid of the class logit
} ai_engine_box_t;

#ifdef __cplusplus
}
#endif
