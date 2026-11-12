/** nhin_task's camera: one full picture a capture, detect run on it, sent and kept; each capture request is
 * handed back to nhin_task (KEHOACH 3.19).
 *  @ctx nhin_task after init | one request at a time
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "app_events.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "gen_payload.h"

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
    app_vision_mode_t mode; // PHOTO, DETECT or JOURNAL
    uint32_t seq;           // names its pictures, note and record
} svc_vision_capture_t;

typedef struct {
    detections_t *found; // until the next request; NULL: detect did not run
    uint32_t bytes;      // the picture's; 0 when none came
    bool sent;           // every chunk reached the outbox
    bool saved;          // the card holds it
} svc_vision_shot_t;

/** Take the buffers; drv_camera and drv_sd come up first, a card may be missing.
 *  @ctx app_boot | blocking | once; nhin_task runs after any result but the first two errors
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_STATE a second call | ESP_ERR_NO_MEM PSRAM refused:
 *       every request is then answered CAMERA_FAIL
 */
esp_err_t svc_vision_init(const svc_vision_config_t *cfg);

/** Wait up to wait_ms for a request; a preview is held here, one frame a call, without waiting.
 *  @ctx nhin_task | blocking up to wait_ms | loop on it
 *  @ret true: a capture waits in q_vision as *capture; carry it out, then call svc_vision_done
 */
bool svc_vision_step(uint32_t wait_ms, svc_vision_capture_t *capture);

/** Whether a preview runs, which keeps nhin_task sending its frames.
 *  @ctx nhin_task | non-blocking
 */
bool svc_vision_previewing(void);

/** Shoot one full picture; with detect, run detect on it and send the boxes; then send it and keep it.
 *  @ctx nhin_task, from svc_vision_step to svc_vision_done | blocking, ~4 s, ~8 s with detect | with sounds,
 * the tone once the answer reached a place
 */
svc_vision_shot_t svc_vision_shoot(uint32_t seq, bool detect, bool sounds);

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
