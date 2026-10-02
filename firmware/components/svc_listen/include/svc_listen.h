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
} svc_listen_decision_t;

/** Read the commands through lang_vi; take the front end, the rings and both lexicon tables from PSRAM.
 *  @ctx task | blocking, allocates | once at boot after ai_engine_load, ahead of nhan_task
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE second call | ESP_ERR_NOT_SUPPORTED no command in the image |
 *       ESP_ERR_INVALID_ARG | APP_ERR_COMMANDS_INVALID a line lang_vi cannot read | ESP_ERR_NO_MEM
 */
esp_err_t svc_listen_init(const svc_listen_config_t *cfg);

/** Read a command set through lang_vi into the spare table and listen with it, the old one kept on failure.
 *  @ctx nhan_task | blocking, lang_vi about 1.2 ms a command | only while no window waits
 *  @param unreadable set to the index of the first line lang_vi cannot read
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no init, or a window waits | ESP_ERR_INVALID_ARG |
 *       APP_ERR_COMMANDS_INVALID a line lang_vi cannot read
 */
esp_err_t svc_listen_set_commands(const svc_listen_commands_t *commands, uint8_t *unreadable);

/** Version of the command set in use; 0 without init.
 *  @ctx nhan_task | non-blocking
 */
uint32_t svc_listen_commands_version(void);

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
