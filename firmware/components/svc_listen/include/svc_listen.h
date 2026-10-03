/** The nhan half of the listener (KEHOACH 3.12, 5.2, 5.4): log-mel of every clean hop and, on an image
 * without wake, a command window for each utterance vad finds, cut as Gate 3 cuts it, stepped through
 * ai_engine while the utterance runs and decided once it closes. Memory is taken once in init; every
 * other call belongs to nhan_task.
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
    const char *const *texts; // command lines, UTF-8; read, not kept
    const char *const *ids;   // their ids, copied
    uint8_t n_commands;       // 1..AI_ENGINE_COMMANDS_MAX
    uint32_t version;         // of the command set the lines come from
} svc_listen_commands_t;

typedef struct {
    svc_listen_commands_t commands;
    lang_vi_dialect_t dialects; // readings the lexicon holds
    uint16_t reject_permille;   // delta1 ai_engine decides with
    uint16_t margin_permille;   // delta2 ai_engine decides with
} svc_listen_config_t;

typedef struct {
    app_event_t event;          // COMMAND or REJECT; seq is the window's last hop
    uint32_t first_seq;         // the window's first hop
    uint16_t free_gap_permille; // free unit loop over the best command
    uint32_t work_us;           // pitch, network and score of the window
    uint32_t close_us;          // from the utterance's close to the decision
} svc_listen_decision_t;

/** Read the commands through lang_vi and prepare ai_engine on them; take the front end, rings and tables.
 *  @ctx task | blocking, allocates in PSRAM | once at boot after ai_engine_load, ahead of nhan_task
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE second call | ESP_ERR_NOT_SUPPORTED no command in the image |
 *       ESP_ERR_INVALID_ARG | APP_ERR_COMMANDS_INVALID a line lang_vi cannot read | ESP_ERR_NO_MEM |
 *       ai_engine_command_prepare's error
 */
esp_err_t svc_listen_init(const svc_listen_config_t *cfg);

/** Read a command set into the spare table, prepare it and listen with it; the old one stays on failure.
 *  @ctx nhan_task | blocking: lang_vi about 1.2 ms a command, then prepare | while svc_listen_busy is false
 *  @param unreadable set to the index of the first line lang_vi cannot read
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no init, or a window open or waiting | ESP_ERR_INVALID_ARG |
 *       APP_ERR_COMMANDS_INVALID a line lang_vi cannot read | ai_engine_command_prepare's error
 */
esp_err_t svc_listen_set_commands(const svc_listen_commands_t *commands, uint8_t *unreadable);

/** Version of the command set in use; 0 without init.
 *  @ctx nhan_task | non-blocking
 */
uint32_t svc_listen_commands_version(void);

/** Take one clean hop into the ring; vad opens a window on a new utterance, extends it, or closes it.
 *  A jump in seq starts afresh: the open window is dropped, and no window reaches across the jump.
 *  @ctx nhan_task | non-blocking | pcm holds GEN_GRID_HOP_SAMPLES samples, copied
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no init | ESP_ERR_NO_MEM a full queue: the utterance gets no window
 */
esp_err_t svc_listen_feed(const int16_t *pcm, uint32_t seq, bool vad);

/** Whether svc_listen_work has a hop to step or a closed window to decide now.
 *  @ctx nhan_task | non-blocking
 */
bool svc_listen_pending(void);

/** Whether a window is open or waits, while the command set cannot change.
 *  @ctx nhan_task | non-blocking
 */
bool svc_listen_busy(void);

/** Step one hop of the oldest window: pitch, then the network, a chunk every 16 hops; decide it once closed.
 *  @ctx nhan_task | blocks for one hop of pitch and at most one chunk of the network, or the decision
 *  @ret true with out filled once the window is decided; false otherwise, or on an error that drops it
 */
bool svc_listen_work(svc_listen_decision_t *out);

#ifdef __cplusplus
}
#endif
