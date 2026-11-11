/** Each capture's record on the card until the server acks it, sent again from the card when the link is
 * back (KEHOACH 3.19).
 *  @ctx nhin_task after init | one call at a time
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
    QueueHandle_t acks; // q_ack: ack_t from net_mqtt
    uint32_t boot;      // this run's: every record's sentBoot
} svc_outbox_config_t;

typedef struct {
    bool saved; // the card holds the record, pending until acked
    bool sent;  // up/record reached the outbox
} svc_outbox_kept_t;

/** Keep the configuration and take a record and its text from PSRAM.
 *  @ctx app_boot | non-blocking | once
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_NO_MEM: captures then go out live only
 */
esp_err_t svc_outbox_init(const svc_outbox_config_t *cfg);

/** Send one capture's record and keep it as <seq>.JSN, marked pending under IMG/PND until acked.
 *  @ctx nhin_task, from svc_vision_step to svc_vision_done | blocking, tens of ms on the card | caller keeps
 * rec; sentBoot, sentUptimeMs and late are filled here
 */
svc_outbox_kept_t svc_outbox_keep(record_t *rec);

/** Take the acks in, then send one chunk or one record of the oldest pending capture when a session is up.
 *  @ctx nhin_task between captures and previews | blocking, a card read and an enqueue
 *  @ret how long nhin_task may wait for a request until the next step, in ms
 */
uint32_t svc_outbox_step(void);

#ifdef __cplusplus
}
#endif
