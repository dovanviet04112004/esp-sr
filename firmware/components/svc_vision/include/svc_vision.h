/** nhin_task's work: photograph, keep, send, and detect on request (KEHOACH 3.19, ADR-0044).
 *  @ctx nhin_task after init | one request at a time
 */
#pragma once

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
} svc_vision_config_t;

/** Take the buffers; drv_camera and drv_sd come up first, a card may be missing.
 *  @ctx app_boot | blocking | once
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_STATE a second call | ESP_ERR_NO_MEM PSRAM refused
 */
esp_err_t svc_vision_init(const svc_vision_config_t *cfg);

/** Carry out one request, waiting a while for it; during a preview, send one frame and do not wait.
 *  @ctx nhin_task | blocking, seconds with a request | loop on it
 */
void svc_vision_step(void);

#ifdef __cplusplus
}
#endif
