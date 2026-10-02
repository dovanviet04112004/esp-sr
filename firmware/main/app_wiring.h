/** The queues, pool, flags and shared stats of KEHOACH 5.3, created in one place.
 *  @ctx task | non-blocking | the only file that creates a queue (KEHOACH 4.5.3 rule 12)
 */
#pragma once

#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/stream_buffer.h"
#include "gen_array.h"
#include "gen_grid.h"
#include "sdkconfig.h"

#ifdef __cplusplus
extern "C" {
#endif

#define APP_FRAME_SLOTS 8
#define APP_FRAME_MAX_CHANNELS (GEN_ARRAY_N_MICS + 1) // two microphones and the speaker reference
#define APP_FRAME_MAX_SAMPLES (GEN_GRID_HOP_SAMPLES * APP_FRAME_MAX_CHANNELS)

typedef struct {
    uint32_t hops;           // hops sach_task finished
    uint32_t frames_dropped; // thu_task found no free slot
    uint32_t clean_dropped;  // q_clean full, hop discarded
    uint32_t events_dropped; // q_event_up full, or no broker to send to
    int16_t doa_deg;         // -1 when unknown
    uint8_t doa_conf;
    uint8_t vad;
    int8_t level_dbfs;
    int8_t gain_db;
    uint16_t flags; // dsp_afe_flag_t bits
} app_afe_stats_t;

typedef struct {
    int16_t pcm[APP_FRAME_MAX_SAMPLES]; // interleaved, drv_audio_channels() per sample
    uint32_t seq;
} app_frame_slot_t;

typedef struct {
    QueueHandle_t frame;         // q_frame: slot index, thu_task -> sach_task
    QueueHandle_t free_slots;    // q_free: slot index, sach_task -> thu_task
    app_frame_slot_t *pool;      // APP_FRAME_SLOTS, internal RAM
    QueueHandle_t clean;         // q_clean: dsp_afe_frame_t, PSRAM
    QueueHandle_t dialog;        // q_dialog: app_event_t, PSRAM
    QueueHandle_t cmd;           // q_cmd: device_cmd_t, PSRAM
    QueueHandle_t cmdset;        // q_cmdset: const net_mqtt_commands_t *
    QueueHandle_t speak;         // q_speak: app_speak_req_t, PSRAM; NULL without a speaker
    QueueHandle_t event_up;      // q_event_up: app_event_t, PSRAM
    StreamBufferHandle_t stream; // sb_stream, PSRAM; NULL without NET_STREAM_ENABLE
    EventGroupHandle_t system;   // eg_system: app_system_bit_t
    app_afe_stats_t *afe_stats;  // s_afe_stats, copied under afe_stats_lock only
    portMUX_TYPE *afe_stats_lock;
} app_wiring_t;

/** Create every queue, fill q_free with all slots, and clear the stats.
 *  @ctx task | blocking | once in app_boot, ahead of app_tasks_start
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE on a second call | ESP_ERR_NO_MEM PSRAM refused a queue
 */
esp_err_t app_wiring_init(void);

/** The one set of handles, valid for the life of the device.
 *  @ctx any | non-blocking
 *  @ret NULL until app_wiring_init has returned ESP_OK
 */
const app_wiring_t *app_wiring(void);

#ifdef __cplusplus
}
#endif
