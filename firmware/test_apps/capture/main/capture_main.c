#include <inttypes.h>
#include <stdbool.h>

#include "app_events.h"
#include "bsp_board.h"
#include "drv_audio.h"
#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_task_wdt.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"
#include "gen_array.h"
#include "gen_grid.h"
#include "gen_stream.h"
#include "net_wifi.h"
#include "sdkconfig.h"
#include "svc_report.h"
#include "sys_storage.h"

#define CAPTURE_CORE 1 // the hop deadline owns core 1, as in main (KEHOACH 5.1)
#define SENDER_CORE 0
#define CAPTURE_PRIORITY 17
#define SENDER_PRIORITY 3
#define CAPTURE_STACK_BYTES 3072
#define SENDER_STACK_BYTES 4096
#define STREAM_BUFFER_BYTES (CONFIG_SVC_REPORT_STREAM_BUFFER_KB * 1024)
#define DMA_DESC_NUM 8
#define DMA_WAIT_MS (2 * GEN_GRID_HOP_US / 1000)
#define LINK_WAIT_MS 1000
#define REPORT_PERIOD_MS 10000
#define HOST_BYTES 64
#define DEVICE_ID_BYTES 33 // deviceId of KEHOACH 6.2: at most 32 characters

#if CONFIG_CAPTURE_REF_ENABLE
#define WITH_REF true
#else
#define WITH_REF false
#endif

static const char *TAG = "capture";

static int16_t s_hop[GEN_GRID_HOP_SAMPLES * (GEN_ARRAY_N_MICS + 1)];

static void capture_task(void *arg)
{
    (void)arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    for (;;) {
        uint32_t seq = 0;
        if (drv_audio_read_frame(s_hop, &seq, DMA_WAIT_MS) == ESP_OK) {
            svc_report_stream_push(seq, s_hop, NULL);
        }
        esp_task_wdt_reset();
    }
}

static void sender_task(void *arg)
{
    (void)arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    for (;;) {
        svc_report_luong_step();
        esp_task_wdt_reset();
    }
}

static uint8_t pcm_shift(void)
{
    uint8_t shift = 0;
    if (sys_storage_get_u8(STORAGE_NS_CALIB, STORAGE_KEY_PCM_SHIFT, &shift) == ESP_OK) { return shift; }
    ESP_LOGW(TAG, "calib/pcm_shift absent, using %d", CONFIG_CAPTURE_PCM_SHIFT_FALLBACK);
    return CONFIG_CAPTURE_PCM_SHIFT_FALLBACK;
}

static void report(EventGroupHandle_t system)
{
    drv_audio_stats_t audio;
    drv_audio_stats(&audio);
    ESP_LOGI(TAG,
             "hops %" PRIu32 ", dma overflows %" PRIu32 ", stream dropped %" PRIu32
             ", internal %u B (min %u), %s",
             audio.hops, audio.dma_overflows, svc_report_stream_dropped(),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
             (xEventGroupGetBits(system) & APP_BIT_STREAM_ON) != 0 ? "streaming" : "stream closed");
}

void app_main(void)
{
    ESP_ERROR_CHECK(bsp_board_init());
    ESP_ERROR_CHECK(sys_storage_init());
    const uint8_t shift = pcm_shift();
    const drv_audio_config_t audio = {
        .pcm_shift = shift, .enable_tx = WITH_REF, .dma_desc_num = DMA_DESC_NUM};
    ESP_ERROR_CHECK(drv_audio_init(&audio));
    // Drain DMA from boot so it never overflows; pushes are dropped quietly until the stream opens.
    xTaskCreatePinnedToCore(capture_task, "capture_task", CAPTURE_STACK_BYTES, NULL, CAPTURE_PRIORITY, NULL,
                            CAPTURE_CORE);

    EventGroupHandle_t system = xEventGroupCreate();
    char device_id[DEVICE_ID_BYTES];
    char host[HOST_BYTES];
    uint16_t port = 0;
    ESP_ERROR_CHECK(sys_storage_device_id(device_id, sizeof(device_id)));
    if (sys_storage_get_str(STORAGE_NS_DEVICE, STORAGE_KEY_STREAM_HOST, host, sizeof(host)) != ESP_OK ||
        sys_storage_get_u16(STORAGE_NS_DEVICE, STORAGE_KEY_STREAM_PORT, &port) != ESP_OK) {
        ESP_LOGE(TAG, "set device/stream_host and device/stream_port from the console of main first");
        return;
    }
    ESP_ERROR_CHECK(net_wifi_init(system, device_id));
    const esp_err_t wifi = net_wifi_apply();
    if (wifi != ESP_OK) {
        ESP_LOGE(TAG, "wifi: %s; set wifi/ssid from the console of main first", esp_err_to_name(wifi));
        return;
    }
    while ((xEventGroupWaitBits(system, APP_BIT_WIFI_OK, pdFALSE, pdTRUE, pdMS_TO_TICKS(LINK_WAIT_MS)) &
            APP_BIT_WIFI_OK) == 0) {}

    StreamBufferHandle_t sb = xStreamBufferCreateWithCaps(STREAM_BUFFER_BYTES, 1, MALLOC_CAP_SPIRAM);
    const svc_report_stream_config_t stream = {
        .sb = sb, .system = system, .raw_channels = drv_audio_channels()};
    ESP_ERROR_CHECK(sb != NULL ? svc_report_stream_init(&stream) : ESP_ERR_NO_MEM);
    const uint8_t mode =
        drv_audio_channels() > GEN_ARRAY_N_MICS ? GEN_STREAM_MODE_RAW_REF : GEN_STREAM_MODE_RAW;
    ESP_ERROR_CHECK(svc_report_stream_start(mode, host, port, CONFIG_CAPTURE_DURATION_S));
    ESP_LOGI(TAG, "esp-sr %s capture %s, grid 0x%08x, pcm_shift %u, mode %u to %s:%u for %d s",
             esp_app_get_description()->version, device_id, (unsigned)GEN_GRID_HASH, (unsigned)shift,
             (unsigned)mode, host, (unsigned)port, CONFIG_CAPTURE_DURATION_S);

    xTaskCreatePinnedToCore(sender_task, "sender_task", SENDER_STACK_BYTES, NULL, SENDER_PRIORITY, NULL,
                            SENDER_CORE);
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(REPORT_PERIOD_MS));
        report(system);
    }
}
