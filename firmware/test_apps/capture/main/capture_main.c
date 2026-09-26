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
#define PUSH_FOREVER UINT32_MAX
#define RADIO_OFF_SLACK_MS 5000
// INMP441 output drifts from about -1400 LSB to 0 in the first 0.5 s after power-up (board B, 26/09).
#define SETTLE_MS 1000

#if CONFIG_CAPTURE_REF_ENABLE
#define WITH_REF true
#else
#define WITH_REF false
#endif

static const char *TAG = "capture";

static int16_t s_hop[GEN_GRID_HOP_SAMPLES * (GEN_ARRAY_N_MICS + 1)];
// app_main sets both while capture_task does not exist yet; from then on only capture_task writes them.
static uint32_t s_hops_to_skip;
static uint32_t s_hops_to_push = PUSH_FOREVER;
static TaskHandle_t s_waiter;

static void capture_task(void *arg)
{
    (void)arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    for (;;) {
        uint32_t seq = 0;
        const bool got = drv_audio_read_frame(s_hop, &seq, DMA_WAIT_MS) == ESP_OK;
        if (got && s_hops_to_skip > 0) {
            s_hops_to_skip--;
        } else if (got && s_hops_to_push > 0) {
            svc_report_stream_push(seq, s_hop, NULL);
            if (s_hops_to_push != PUSH_FOREVER && --s_hops_to_push == 0) { xTaskNotifyGive(s_waiter); }
        }
        esp_task_wdt_reset();
    }
}

static uint32_t hops_in_ms(uint64_t ms)
{
    const uint64_t samples = ms * GEN_GRID_SAMPLE_RATE_HZ / 1000;
    return (uint32_t)((samples + GEN_GRID_HOP_SAMPLES - 1) / GEN_GRID_HOP_SAMPLES);
}

static uint32_t radio_off_hops(void)
{
    return hops_in_ms((uint64_t)CONFIG_CAPTURE_RADIO_OFF_S * 1000);
}

static size_t stream_buffer_bytes(size_t channels)
{
    const size_t frame_bytes = GEN_STREAM_HEADER_BYTES + GEN_GRID_HOP_SAMPLES * channels * sizeof(int16_t);
    const size_t held_bytes = (size_t)(radio_off_hops() + 1) * frame_bytes;
    return held_bytes > STREAM_BUFFER_BYTES ? held_bytes : STREAM_BUFFER_BYTES;
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
    char device_id[DEVICE_ID_BYTES];
    char host[HOST_BYTES];
    uint16_t port = 0;
    ESP_ERROR_CHECK(sys_storage_device_id(device_id, sizeof(device_id)));
    if (sys_storage_get_str(STORAGE_NS_DEVICE, STORAGE_KEY_STREAM_HOST, host, sizeof(host)) != ESP_OK ||
        sys_storage_get_u16(STORAGE_NS_DEVICE, STORAGE_KEY_STREAM_PORT, &port) != ESP_OK) {
        ESP_LOGE(TAG, "set device/stream_host and device/stream_port from the console of main first");
        return;
    }
    const drv_audio_config_t audio = {
        .pcm_shift = shift, .enable_tx = WITH_REF, .dma_desc_num = DMA_DESC_NUM};
    ESP_ERROR_CHECK(drv_audio_init(&audio));

    EventGroupHandle_t system = xEventGroupCreate();
    StreamBufferHandle_t sb =
        xStreamBufferCreateWithCaps(stream_buffer_bytes(drv_audio_channels()), 1, MALLOC_CAP_SPIRAM);
    const svc_report_stream_config_t stream = {
        .sb = sb, .system = system, .raw_channels = drv_audio_channels()};
    ESP_ERROR_CHECK(sb != NULL ? svc_report_stream_init(&stream) : ESP_ERR_NO_MEM);
    const uint8_t mode =
        drv_audio_channels() > GEN_ARRAY_N_MICS ? GEN_STREAM_MODE_RAW_REF : GEN_STREAM_MODE_RAW;
    const bool radio_off = CONFIG_CAPTURE_RADIO_OFF_S > 0;
    if (radio_off) {
        s_hops_to_skip = hops_in_ms(SETTLE_MS);
        s_hops_to_push = radio_off_hops();
        s_waiter = xTaskGetCurrentTaskHandle();
        ESP_ERROR_CHECK(svc_report_stream_start(mode, host, port, CONFIG_CAPTURE_DURATION_S));
    }
    // Drain DMA from boot so it never overflows; pushes are dropped quietly until the stream opens.
    xTaskCreatePinnedToCore(capture_task, "capture_task", CAPTURE_STACK_BYTES, NULL, CAPTURE_PRIORITY, NULL,
                            CAPTURE_CORE);
    if (radio_off) {
        const uint32_t wait_ms = CONFIG_CAPTURE_RADIO_OFF_S * 1000 + SETTLE_MS + RADIO_OFF_SLACK_MS;
        if (ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(wait_ms)) == 0) {
            ESP_LOGE(TAG, "radio-off recording did not finish in %u ms", (unsigned)wait_ms);
            return;
        }
        ESP_LOGI(TAG, "recorded %u hops with the radio off, %u dropped; joining Wi-Fi to send them",
                 (unsigned)radio_off_hops(), (unsigned)svc_report_stream_dropped());
    }

    ESP_ERROR_CHECK(net_wifi_init(system, device_id));
    const esp_err_t wifi = net_wifi_apply();
    if (wifi != ESP_OK) {
        ESP_LOGE(TAG, "wifi: %s; set wifi/ssid from the console of main first", esp_err_to_name(wifi));
        return;
    }
    while ((xEventGroupWaitBits(system, APP_BIT_WIFI_OK, pdFALSE, pdTRUE, pdMS_TO_TICKS(LINK_WAIT_MS)) &
            APP_BIT_WIFI_OK) == 0) {}
    if (radio_off) {
        net_wifi_set_low_latency(true);
    } else {
        ESP_ERROR_CHECK(svc_report_stream_start(mode, host, port, CONFIG_CAPTURE_DURATION_S));
    }
    ESP_LOGI(TAG,
             "esp-sr %s capture %s, grid 0x%08x, pcm_shift %u, mode %u to %s:%u for %d s, radio off %d s",
             esp_app_get_description()->version, device_id, (unsigned)GEN_GRID_HASH, (unsigned)shift,
             (unsigned)mode, host, (unsigned)port, CONFIG_CAPTURE_DURATION_S, CONFIG_CAPTURE_RADIO_OFF_S);

    xTaskCreatePinnedToCore(sender_task, "sender_task", SENDER_STACK_BYTES, NULL, SENDER_PRIORITY, NULL,
                            SENDER_CORE);
    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(REPORT_PERIOD_MS));
        report(system);
    }
}
