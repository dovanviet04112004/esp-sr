/** The nhan half of the listener (KEHOACH 3.12, 5.2, 5.4): log-mel of every clean hop and, on an image
 * without wake, each utterance vad finds cut into a command window as Gate 3 cuts it and decided by
 * ai_engine. Memory is taken once in init; every other call belongs to nhan_task.
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

typedef struct {
    const char *const *texts;   // command lines, UTF-8; read, not kept
    const char *const *ids;     // their ids, copied
    uint8_t n_commands;         // 1..AI_ENGINE_COMMANDS_MAX
    lang_vi_dialect_t dialects; // readings the lexicon holds
    uint16_t reject_permille;   // delta1 ai_engine decides with
    uint16_t margin_permille;   // delta2 ai_engine decides with
} svc_listen_config_t;

typedef struct {
    app_event_t event;          // COMMAND or REJECT; seq is the window's last hop
    uint32_t first_seq;         // the window's first hop
    uint16_t free_gap_permille; // free unit loop over the best command
    uint32_t work_us;           // pitch, network and score of the window
} svc_listen_decision_t;

/** Build the lexicon of the commands through lang_vi and take the front end and the rings from PSRAM.
 *  @ctx task | blocking, allocates | once at boot after ai_engine_load, ahead of nhan_task
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE second call | ESP_ERR_NOT_SUPPORTED no command in the image |
 *       ESP_ERR_INVALID_ARG a line lang_vi cannot read | ESP_ERR_NO_MEM
 */
esp_err_t svc_listen_init(const svc_listen_config_t *cfg);

/** Take one clean hop into the ring; vad extends or closes the utterance, a long one queuing its window.
 *  A jump in seq starts afresh, and no window reaches across it.
 *  @ctx nhan_task | non-blocking | pcm holds GEN_GRID_HOP_SAMPLES samples, copied
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no init | ESP_ERR_NO_MEM a full queue: the newest window is dropped
 */
esp_err_t svc_listen_feed(const int16_t *pcm, uint32_t seq, bool vad);

/** Whether a queued window waits for svc_listen_work.
 *  @ctx nhan_task | non-blocking
 */
bool svc_listen_pending(void);

/** Run one hop of the oldest queued window: pitch, then the network, a chunk every 16 hops; score at its end.
 *  @ctx nhan_task | blocks for one hop of pitch and at most one chunk of the network
 *  @ret true with out filled once the window is decided; false otherwise, or on an error that drops it
 */
bool svc_listen_work(svc_listen_decision_t *out);

#ifdef __cplusplus
}
#endif
