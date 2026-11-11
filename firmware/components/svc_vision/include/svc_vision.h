/** nhin_task's camera: photograph, keep, send and detect on request; a journal entry is handed back
 * (KEHOACH 3.19).
 *  @ctx nhin_task after init | one request at a time
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    QueueHandle_t requests;   // q_vision: app_vision_req_t
    QueueHandle_t events;     // q_event_up: app_event_t
    uint32_t boot;            // names the pictures of this run
    uint32_t detect_priority; // the task's for one detect run, above the listener
    QueueHandle_t speak;      // q_speak: app_speak_req_t; NULL without a speaker
} svc_vision_config_t;

typedef struct {
    uint32_t bytes;
    bool sent;  // every chunk reached the outbox
    bool saved; // the card holds it
} svc_vision_full_t;

/** Take the buffers; drv_camera and drv_sd come up first, a card may be missing.
 *  @ctx app_boot | blocking | once; nhin_task runs after any result but the first two errors
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_STATE a second call | ESP_ERR_NO_MEM PSRAM refused:
 *       every request is then answered CAMERA_FAIL
 */
esp_err_t svc_vision_init(const svc_vision_config_t *cfg);

/** Carry out one request, waiting a while for it; during a preview, send one frame and do not wait.
 *  @ctx nhin_task | blocking, seconds with a request | loop on it
 *  @ret true: a journal request waits in q_vision under *journal_seq; keep it, then call svc_vision_done
 */
bool svc_vision_step(uint32_t *journal_seq);

/** Shoot detect's frame and send it, then what detect finds in it, with no tone.
 *  @ctx nhin_task, from svc_vision_step to svc_vision_done | blocking, ~3.5 s
 */
void svc_vision_detect(uint32_t seq);

/** Shoot the full picture, send it and keep it on the card, with no tone.
 *  @ctx nhin_task, from svc_vision_step to svc_vision_done | blocking, ~4 s
 */
svc_vision_full_t svc_vision_full(uint32_t seq);

/** Let q_vision take the next request once the handed-back one is done.
 *  @ctx nhin_task | non-blocking
 */
void svc_vision_done(void);

/** Events lost to a full q_event_up, for the heartbeat's eventsDropped.
 *  @ctx any | non-blocking
 */
uint32_t svc_vision_events_lost(void);

#ifdef __cplusplus
}
#endif
