/** One capture of "chụp ảnh", "nhận diện" or "ghi nhật ký": svc_vision's pictures, svc_note's voice note,
 * then the record svc_outbox sends and keeps (KEHOACH 3.19).
 *  @ctx nhin_task after init | one capture at a time
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "gen_payload.h"
#include "svc_vision.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    QueueHandle_t speak;             // q_speak: app_speak_req_t; NULL without a speaker
    uint32_t boot;                   // the record's and the card's
    bool (*sensors)(sensors_t *out); // the latest reading; NULL with the sensors off
} svc_capture_config_t;

/** Keep the configuration and take room for one record from PSRAM.
 *  @ctx app_boot | non-blocking | once
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_NO_MEM: captures then leave no record
 */
esp_err_t svc_capture_init(const svc_capture_config_t *cfg);

/** Carry out the capture svc_vision_step handed out, then send and keep its record.
 *  @ctx nhin_task, from svc_vision_step to svc_vision_done | blocking, ~8 s, a journal note.max_s more
 */
void svc_capture_keep(const svc_vision_capture_t *capture);

#ifdef __cplusplus
}
#endif
