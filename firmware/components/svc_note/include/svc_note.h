/** A journal entry's voice note: cleaned hops from sb_note to a WAV on the card, then up/note (KEHOACH 3.19).
 *  @ctx ghi_task records, nhin_task starts, waits and sends | one note at a time
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/stream_buffer.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    StreamBufferHandle_t hops; // sb_note: app_note_hop_t from nhan_task
    EventGroupHandle_t system; // eg_system: APP_BIT_NOTE_ON opens sb_note
    QueueHandle_t requests;    // q_note: uint32_t seq, nhin_task to ghi_task
    QueueHandle_t results;     // q_note_done: svc_note_t, ghi_task to nhin_task
    QueueHandle_t speak;       // q_speak: app_speak_req_t; NULL without a speaker
    uint32_t boot;             // names the notes of this run
} svc_note_config_t;

typedef struct {
    uint32_t seq;     // the entry's
    uint32_t ms;      // hops taken, as time
    uint32_t bytes;   // the WAV file's, header included; 0 unless saved
    uint32_t dropped; // hops missing between those that came
    bool saved;       // the card holds the whole note, header patched
} svc_note_t;

/** Take the card block and one hop's record from PSRAM.
 *  @ctx app_boot | blocking briefly | once
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_STATE a second call | ESP_ERR_NO_MEM: notes are not
 *       saved
 */
esp_err_t svc_note_init(const svc_note_config_t *cfg);

/** Wait a while for a request; record it until the talker stops, with the light held, beep, hand the result
 * back.
 *  @ctx ghi_task | blocking, up to note.max_s with a request | loop on it
 */
void svc_note_step(void);

/** Ask ghi_task for a note under seq, kept as IMG/B<boot>/<seq>.WAV.
 *  @ctx nhin_task | non-blocking
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no queue, or a request still waiting
 */
esp_err_t svc_note_start(uint32_t seq);

/** Wait for the note under seq; results of an older note are passed over.
 *  @ctx nhin_task | blocking, note.max_s and 10 s at most | note says saved false when none came
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no queue | ESP_ERR_TIMEOUT
 */
esp_err_t svc_note_wait(uint32_t seq, svc_note_t *note);

/** Send the note recorded last, read back from the card, on up/note under its seq.
 *  @ctx nhin_task, after svc_note_wait | blocking on the network
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE nothing saved | ESP_ERR_INVALID_SIZE the file came up short | the
 *       error of drv_sd_read or net_mqtt_publish_chunks
 */
esp_err_t svc_note_send(void);

#ifdef __cplusplus
}
#endif
