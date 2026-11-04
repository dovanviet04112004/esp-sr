/** noi_task's work: what goes out of the speaker (KEHOACH 3.19).
 *  @ctx noi_task after init | one request at a time
 */
#pragma once

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    QueueHandle_t requests; // q_speak: app_speak_req_t
} svc_speak_config_t;

/** Keep the queue this service serves.
 *  @ctx app_boot | non-blocking | once
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG
 */
esp_err_t svc_speak_init(const svc_speak_config_t *cfg);

/** Wait a while for one request and play it.
 *  @ctx noi_task | blocking, as long as the sound | loop on it
 */
void svc_speak_step(void);

#ifdef __cplusplus
}
#endif
