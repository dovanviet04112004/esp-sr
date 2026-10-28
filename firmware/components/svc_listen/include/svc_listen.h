/** The nhan half of the listener (KEHOACH 3.12, 5.2, 5.4): log-mel of every clean hop and, on an image
 * without wake, a command window for each utterance vad finds, cut as Gate 3 cuts it, stepped through
 * ai_engine while the utterance runs and decided once it closes. With speaker in the image, xac_task checks
 * the speaker of each command through the verify calls (KEHOACH 3.17). Memory is taken once in init; every
 * other call belongs to nhan_task, the verify and speaker calls to xac_task.
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "app_events.h"
#include "esp_err.h"
#include "lang_vi.h"

#ifdef __cplusplus
extern "C" {
#endif

#define SVC_LISTEN_VERIFY_SLOTS 2 // windows waiting for xac_task, in PSRAM (KEHOACH 5.3)

typedef struct {
    const char *const *texts; // command lines, UTF-8; read, not kept
    const char *const *ids;   // their ids, copied
    uint16_t n_commands;      // 1..AI_ENGINE_COMMANDS_MAX
    uint32_t version;         // of the command set the lines come from
} svc_listen_commands_t;

typedef struct {
    svc_listen_commands_t commands;
    lang_vi_dialect_t dialects; // readings the lexicon holds
    uint16_t reject_permille;   // delta1 ai_engine decides with
    uint16_t margin_permille;   // delta2 ai_engine decides with
    uint16_t syllable_permille; // delta3 ai_engine decides with
    uint16_t enrol_window_s;    // an unfinished enrolment ends this long after ENROL
} svc_listen_config_t;

typedef struct {
    app_event_t event;              // COMMAND or REJECT; seq is the window's last hop
    uint32_t first_seq;             // the window's first hop
    uint32_t speech_seq;            // its utterance's first vad hop
    uint16_t free_gap_permille;     // free unit loop over the best command
    uint16_t syllable_gap_permille; // free loop over its worst syllable
    uint32_t work_us;               // network and score of the window
    uint32_t close_us;              // from the utterance's close to the decision
    uint32_t verify_us;             // the speaker check, 0 without one
    int16_t speaker_milli_z;        // the best voiceprint's score, with has_speaker
    uint8_t noise_bin;              // of the speaker check, ADR-0024
} svc_listen_decision_t;

/** Read the commands, prepare ai_engine, take the rings and tables, and with speaker the PCM ring, the verify
 *  slots, the voiceprints and the roster of NVS speaker/ (KEHOACH 3.17, 6.2).
 *  @ctx task | blocking, allocates in PSRAM | once at boot after ai_engine_load, ahead of every task
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE | ESP_ERR_NOT_SUPPORTED no command | ESP_ERR_INVALID_SIZE bands |
 *       ESP_ERR_INVALID_ARG | ESP_ERR_NO_MEM | APP_ERR_COMMANDS_INVALID | ai_engine_command_prepare's error
 */
esp_err_t svc_listen_init(const svc_listen_config_t *cfg);

/** Read a command set into the spare table, prepare it and listen with it; the old one stays on failure.
 *  @ctx nhan_task | blocking: lang_vi about 1.2 ms a command, then prepare | while svc_listen_busy is false
 *  @param unreadable set to the index of the first line lang_vi cannot read
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no init, or a window open or waiting | ESP_ERR_INVALID_ARG |
 *       APP_ERR_COMMANDS_INVALID a line lang_vi cannot read | ai_engine_command_prepare's error
 */
esp_err_t svc_listen_set_commands(const svc_listen_commands_t *commands, uint16_t *unreadable);

/** Version of the command set in use; 0 without init.
 *  @ctx nhan_task | non-blocking
 */
uint32_t svc_listen_commands_version(void);

/** Take one clean hop into the rings with its log-mel and pitch; vad opens, extends or closes a window.
 *  A jump in seq, or gap (the chain broke ahead of this hop), starts afresh: STFT and pitch reset, no window
 *  reaches across, and the open window, its audio holed, is decided REJECT FRAME_GAP unscored (KEHOACH 5.4).
 *  @ctx nhan_task | non-blocking, about 2 ms of pitch | pcm holds GEN_GRID_HOP_SAMPLES samples, copied
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no init | ESP_ERR_NO_MEM a full queue: the utterance gets no window
 */
esp_err_t svc_listen_feed(const int16_t *pcm, uint32_t seq, bool vad, bool gap);

/** Whether svc_listen_work has a hop to step or a closed window to decide now.
 *  @ctx nhan_task | non-blocking
 */
bool svc_listen_pending(void);

/** Whether a window is open or waits, while the command set cannot change.
 *  @ctx nhan_task | non-blocking
 */
bool svc_listen_busy(void);

/** Step one hop of the oldest window into the network, a chunk every 16 hops; decide it once closed.
 *  @ctx nhan_task | blocks for at most one chunk of the network, or the decision
 *  @ret true with out filled once the window is decided; false otherwise, or on an error that drops it
 */
bool svc_listen_work(svc_listen_decision_t *out);

/** Whether a COMMAND goes to xac_task: speaker in the image, and checking on or an enrolment running.
 *  @ctx nhan_task | non-blocking | reads a flag xac_task writes
 */
bool svc_listen_verifies(void);

/** Copy a COMMAND and the last GEN_SPEAKER_WINDOW_SAMPLES of its window's clean PCM into a verify slot.
 *  @ctx nhan_task | non-blocking | the caller holds slot, taken from q_verify_free, and hands it to q_verify
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_NOT_FOUND the ring moved past the window's tail
 */
esp_err_t svc_listen_hold(const svc_listen_decision_t *decision, uint8_t slot);

/** Turn a COMMAND no slot could take into REJECT SPEAKER_BUSY, its command id kept.
 *  @ctx nhan_task | non-blocking
 */
void svc_listen_busy_reject(svc_listen_decision_t *decision);

/** Check the speaker of the slot's COMMAND: one speaker run, the best AS-norm score over the voiceprints, the
 * decision with the best one's id and cosine; an enrolment takes the command instead, REJECT
 * SPEAKER_ENROLLING (KEHOACH 3.17, ADR-0023).
 *  @ctx xac_task | blocking ~1.5 s of core 0 (b3); writes NVS when an enrolment ends | the caller holds slot
 *  @ret ESP_OK, out filled | ESP_ERR_INVALID_ARG | APP_ERR_SPEAKERS_NOT_SAVED a voiceprint in use, not in NVS
 */
esp_err_t svc_listen_verify(uint8_t slot, svc_listen_decision_t *out);

/** Take a roster as KEHOACH 3.17 lays out SET: one not above the held version changes nothing; an id dropped
 * loses its voiceprint and its enrolment at once; a new id takes the lowest free slot; NVS as KEHOACH 6.2.
 *  @ctx xac_task | blocking on flash
 *  @ret ESP_OK, ignored or in use | ESP_ERR_NOT_SUPPORTED no speaker | ESP_ERR_INVALID_ARG |
 *       APP_ERR_SPEAKERS_INVALID the held one stays | APP_ERR_SPEAKERS_NOT_SAVED in use, NVS not written
 */
esp_err_t svc_listen_speaker_set(const app_speaker_set_t *set);

/** Enrol id: its next GEN_SPEAKER_ENROL_COMMANDS commands within enrol_window_s become its voiceprint;
 * another enrolment ends.
 *  @ctx xac_task | non-blocking
 *  @ret ESP_OK | ESP_ERR_NOT_SUPPORTED no speaker | ESP_ERR_INVALID_ARG | APP_ERR_SPEAKER_UNKNOWN not listed
 */
esp_err_t svc_listen_speaker_enrol(const char *id);

/** End the enrolment under way, if any; every voiceprint stays as it is (KEHOACH 3.17).
 *  @ctx xac_task | non-blocking
 *  @ret ESP_OK, also with none under way | ESP_ERR_NOT_SUPPORTED no speaker
 */
esp_err_t svc_listen_speaker_enrol_cancel(void);

/** End an enrolment enrol_window_s old that still lacks commands (KEHOACH 3.17).
 *  @ctx xac_task | non-blocking
 */
void svc_listen_speaker_expire(void);

/** The roster as it stands, for q_roster; serial rises with each change.
 *  @ctx xac_task | non-blocking
 *  @ret ESP_OK | ESP_ERR_NOT_SUPPORTED no speaker in the image | ESP_ERR_INVALID_ARG
 */
esp_err_t svc_listen_speaker_state(app_speaker_state_t *out);

#ifdef __cplusplus
}
#endif
