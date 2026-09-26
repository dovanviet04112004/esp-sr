#include "app_wiring.h"

#include <stdbool.h>

#include "app_events.h"
#include "dsp_afe.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "gen_payload.h"

#define CLEAN_DEPTH 64 // about one second of hops
#define DIALOG_DEPTH 8
#define CMD_DEPTH 4
#define CMDSET_DEPTH 1
#define SPEAK_DEPTH 4
#define EVENT_UP_DEPTH 16

static const char *TAG = "app_wiring";

static app_wiring_t s_wiring;
static bool s_ready;
static size_t s_psram_bytes;

static app_frame_slot_t s_pool[APP_FRAME_SLOTS];
static uint8_t s_frame_items[APP_FRAME_SLOTS];
static uint8_t s_free_items[APP_FRAME_SLOTS];
static uint8_t s_cmdset_items[CMDSET_DEPTH * sizeof(const command_set_t *)];
static StaticQueue_t s_frame_q, s_free_q, s_clean_q, s_dialog_q, s_cmd_q, s_cmdset_q, s_event_up_q;
#if CONFIG_APP_SPEAKER_ENABLE
static StaticQueue_t s_speak_q;
#endif
static StaticEventGroup_t s_system_eg;
static app_afe_stats_t s_afe_stats;
static portMUX_TYPE s_afe_stats_lock = portMUX_INITIALIZER_UNLOCKED;

static QueueHandle_t psram_queue(size_t depth, size_t item_bytes, StaticQueue_t *queue)
{
    uint8_t *items = heap_caps_calloc(depth, item_bytes, MALLOC_CAP_SPIRAM);
    if (items == NULL) { return NULL; }
    s_psram_bytes += depth * item_bytes;
    return xQueueCreateStatic(depth, item_bytes, items, queue);
}

esp_err_t app_wiring_init(void)
{
    if (s_ready) { return ESP_ERR_INVALID_STATE; }
    s_wiring.pool = s_pool;
    s_wiring.frame = xQueueCreateStatic(APP_FRAME_SLOTS, sizeof(uint8_t), s_frame_items, &s_frame_q);
    s_wiring.free_slots = xQueueCreateStatic(APP_FRAME_SLOTS, sizeof(uint8_t), s_free_items, &s_free_q);
    s_wiring.clean = psram_queue(CLEAN_DEPTH, sizeof(dsp_afe_frame_t), &s_clean_q);
    s_wiring.dialog = psram_queue(DIALOG_DEPTH, sizeof(app_event_t), &s_dialog_q);
    s_wiring.cmd = psram_queue(CMD_DEPTH, sizeof(device_cmd_t), &s_cmd_q);
    s_wiring.cmdset =
        xQueueCreateStatic(CMDSET_DEPTH, sizeof(const command_set_t *), s_cmdset_items, &s_cmdset_q);
    s_wiring.event_up = psram_queue(EVENT_UP_DEPTH, sizeof(app_event_t), &s_event_up_q);
    s_wiring.system = xEventGroupCreateStatic(&s_system_eg);
    s_wiring.afe_stats = &s_afe_stats;
    s_wiring.afe_stats_lock = &s_afe_stats_lock;
    s_afe_stats.doa_deg = -1;
#if CONFIG_APP_SPEAKER_ENABLE
    s_wiring.speak = psram_queue(SPEAK_DEPTH, sizeof(app_speak_req_t), &s_speak_q);
    if (s_wiring.speak == NULL) { return ESP_ERR_NO_MEM; }
#endif
    if (s_wiring.clean == NULL || s_wiring.dialog == NULL || s_wiring.cmd == NULL ||
        s_wiring.event_up == NULL) {
        return ESP_ERR_NO_MEM;
    }
    for (uint8_t slot = 0; slot < APP_FRAME_SLOTS; slot++) {
        if (xQueueSend(s_wiring.free_slots, &slot, 0) != pdTRUE) { return ESP_ERR_INVALID_STATE; }
    }
    s_ready = true;
    ESP_LOGI(TAG, "pool %u x %u B internal, queues %u B in PSRAM", (unsigned)APP_FRAME_SLOTS,
             (unsigned)sizeof(app_frame_slot_t), (unsigned)s_psram_bytes);
    return ESP_OK;
}

const app_wiring_t *app_wiring(void)
{
    return s_ready ? &s_wiring : NULL;
}
