/** One journal entry: the voice note, detect's frame and boxes, the full picture, then up/journal
 * (KEHOACH 3.19).
 *  @ctx nhin_task after init | one entry at a time
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "gen_payload.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    QueueHandle_t speak;             // q_speak: app_speak_req_t; NULL without a speaker
    uint32_t boot;                   // up/journal's and the card's
    bool (*sensors)(sensors_t *out); // the latest reading; NULL with the sensors off
} svc_journal_config_t;

/** Keep the configuration and take room for one sensor reading from PSRAM.
 *  @ctx app_boot | non-blocking | once
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_NO_MEM: entries then leave no .JSN
 */
esp_err_t svc_journal_init(const svc_journal_config_t *cfg);

/** Keep one entry under the seq svc_vision_step handed out: note and pictures at once, then up/journal and
 * .JSN.
 *  @ctx nhin_task, from svc_vision_step to svc_vision_done | blocking, note.max_s and ~10 s at most
 */
void svc_journal_keep(uint32_t seq);

#ifdef __cplusplus
}
#endif
