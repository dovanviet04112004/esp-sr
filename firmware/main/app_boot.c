#include "app_boot.h"

#include <inttypes.h>
#include <stdbool.h>

#include "ai_engine.h"
#include "app_wiring.h"
#include "bsp_board.h"
#include "drv_audio.h"
#include "esp_app_desc.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "gen_grid.h"
#include "gen_topics.h"
#include "net_mqtt.h"
#include "sdkconfig.h"
#include "sys_storage.h"

#define DMA_DESC_NUM 8       // 128 ms of hops outlasts one flash erase (KEHOACH 5.5)
#define MODEL_SLOT_DEFAULT 0 // model/active_slot absent: models_0

#if CONFIG_APP_SPEAKER_ENABLE
#define SPEAKER_FITTED true
#else
#define SPEAKER_FITTED false
#endif

static const char *TAG = "app_boot";

static void log_heap(const char *step)
{
    ESP_LOGI(TAG, "%-8s internal %u B free, %u B largest block; psram %u B free", step,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
}

static uint8_t pcm_shift(void)
{
    uint8_t shift = 0;
    if (sys_storage_get_u8(STORAGE_NS_CALIB, STORAGE_KEY_PCM_SHIFT, &shift) == ESP_OK) { return shift; }
    ESP_LOGW(TAG, "calib/pcm_shift absent, using %d", CONFIG_APP_PCM_SHIFT_FALLBACK);
    return CONFIG_APP_PCM_SHIFT_FALLBACK;
}

static void load_models(void)
{
    uint8_t slot = MODEL_SLOT_DEFAULT;
    if (sys_storage_get_u8(STORAGE_NS_MODEL, STORAGE_KEY_ACTIVE_SLOT, &slot) != ESP_OK) {
        slot = MODEL_SLOT_DEFAULT;
    }
    const esp_err_t err = ai_engine_load(slot);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "no models from slot %u (%s), running without them", slot, esp_err_to_name(err));
    }
}

esp_err_t app_boot(void)
{
    ESP_RETURN_ON_ERROR(bsp_board_init(), TAG, "board");
    ESP_RETURN_ON_ERROR(sys_storage_init(), TAG, "storage");
    log_heap("storage");
    ESP_RETURN_ON_ERROR(app_wiring_init(), TAG, "wiring");
    log_heap("wiring");
    ESP_RETURN_ON_ERROR(net_mqtt_init(), TAG, "json arenas");
    const drv_audio_config_t audio = {
        .pcm_shift = pcm_shift(),
        .enable_tx = SPEAKER_FITTED,
        .dma_desc_num = DMA_DESC_NUM,
    };
    ESP_RETURN_ON_ERROR(drv_audio_init(&audio), TAG, "audio");
    log_heap("audio");
    load_models();
    log_heap("models");

    char device_id[GEN_TOPIC_DEVICE_ID_MAX + 1];
    ESP_RETURN_ON_ERROR(sys_storage_device_id(device_id, sizeof(device_id)), TAG, "device id");
    ESP_LOGI(TAG, "esp-sr %s, grid 0x%08x, %s, boot %" PRIu32 ", %u channel(s), speaker %s",
             esp_app_get_description()->version, (unsigned)GEN_GRID_HASH, device_id, sys_storage_boot_count(),
             (unsigned)drv_audio_channels(), SPEAKER_FITTED ? "on" : "off");
    return ESP_OK;
}
