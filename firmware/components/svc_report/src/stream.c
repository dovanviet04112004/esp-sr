#include <stdatomic.h>
#include <stdbool.h>
#include <string.h>

#include "app_events.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gen_array.h"
#include "gen_grid.h"
#include "gen_stream.h"
#include "net_stream.h"
#include "sdkconfig.h"
#include "svc_report.h"

#define STREAM_CHANNELS_MAX 4
#define FRAME_MAX_BYTES                                                                                      \
    (GEN_STREAM_HEADER_BYTES + GEN_GRID_HOP_SAMPLES * STREAM_CHANNELS_MAX * sizeof(int16_t))
#define HOST_BYTES 64
#define IDLE_WAIT_MS 500
#define RECEIVE_WAIT_MS 100
#define RECONNECT_BACKOFF_US 1000000LL
#define US_PER_S 1000000LL

static const char *TAG = "svc_report";

typedef union {
    uint8_t bytes[FRAME_MAX_BYTES];
    int16_t align; // PCM follows the 24-byte header on a 2-byte boundary
} frame_buf_t;

static struct {
    StreamBufferHandle_t sb;
    EventGroupHandle_t system;
    uint8_t raw_channels;
    _Atomic uint8_t mode;
    _Atomic uint32_t dropped;
    portMUX_TYPE lock; // host, port, until_us: start runs on another task
    char host[HOST_BYTES];
    uint16_t port;
    int64_t until_us;
    uint8_t sent_mode;       // luong_task only
    int64_t next_connect_us; // luong_task only
    bool told_failure;       // luong_task only
    frame_buf_t out;         // sach_task only
    frame_buf_t in;          // luong_task only
} s_stream = {.lock = portMUX_INITIALIZER_UNLOCKED};

static bool wants_clean(uint8_t mode)
{
    return mode == GEN_STREAM_MODE_CLEAN || mode == GEN_STREAM_MODE_RAW_REF_CLEAN;
}

static uint8_t raw_needed(uint8_t mode)
{
    const uint8_t channels = gen_stream_mode_channels((gen_stream_mode_t)mode);
    return wants_clean(mode) ? channels - 1 : channels;
}

esp_err_t svc_report_stream_init(const svc_report_stream_config_t *cfg)
{
    if (s_stream.sb != NULL) { return ESP_ERR_INVALID_STATE; }
    if (cfg == NULL || cfg->sb == NULL || cfg->system == NULL || cfg->raw_channels < GEN_ARRAY_N_MICS) {
        return ESP_ERR_INVALID_ARG;
    }
    s_stream.system = cfg->system;
    s_stream.raw_channels = cfg->raw_channels;
    s_stream.sb = cfg->sb;
    return ESP_OK;
}

esp_err_t svc_report_stream_start(uint8_t mode, const char *host, uint16_t port, uint32_t duration_s)
{
    if (s_stream.sb == NULL || mode == GEN_STREAM_MODE_OFF ||
        gen_stream_mode_channels((gen_stream_mode_t)mode) == 0 || host == NULL || host[0] == '\0' ||
        strlen(host) >= HOST_BYTES || port == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (raw_needed(mode) > s_stream.raw_channels) { return ESP_ERR_NOT_SUPPORTED; }
    const uint32_t seconds = duration_s == 0 || duration_s > CONFIG_SVC_REPORT_STREAM_MAX_S
                                 ? CONFIG_SVC_REPORT_STREAM_MAX_S
                                 : duration_s;
    taskENTER_CRITICAL(&s_stream.lock);
    strlcpy(s_stream.host, host, sizeof(s_stream.host));
    s_stream.port = port;
    s_stream.until_us = esp_timer_get_time() + (int64_t)seconds * US_PER_S;
    taskEXIT_CRITICAL(&s_stream.lock);
    atomic_store(&s_stream.mode, mode);
    xEventGroupSetBits(s_stream.system, APP_BIT_STREAM_ON);
    ESP_LOGI(TAG, "stream mode %u to %s:%u for %u s", (unsigned)mode, host, (unsigned)port,
             (unsigned)seconds);
    return ESP_OK;
}

void svc_report_stream_stop(void)
{
    if (s_stream.sb == NULL) { return; }
    atomic_store(&s_stream.mode, GEN_STREAM_MODE_OFF);
    xEventGroupClearBits(s_stream.system, APP_BIT_STREAM_ON);
}

static size_t build_frame(uint8_t mode, uint32_t seq, const int16_t *raw, const int16_t *clean)
{
    const uint8_t channels = gen_stream_mode_channels((gen_stream_mode_t)mode);
    const uint8_t from_raw = raw_needed(mode);
    const bool add_clean = wants_clean(mode);
    const gen_stream_header_t header = {
        .magic = GEN_STREAM_MAGIC,
        .version = GEN_STREAM_VERSION,
        .mode = mode,
        .t_us = (uint64_t)esp_timer_get_time(),
        .seq = seq,
        .channels = channels,
        .format = GEN_STREAM_FORMAT_S16LE,
        .samples = GEN_GRID_HOP_SAMPLES,
    };
    memcpy(s_stream.out.bytes, &header, sizeof(header));
    int16_t *pcm = (int16_t *)(s_stream.out.bytes + sizeof(header));
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        for (uint8_t c = 0; c < from_raw; c++) {
            *pcm++ = raw[i * s_stream.raw_channels + c];
        }
        if (add_clean) { *pcm++ = clean[i]; }
    }
    return sizeof(header) + GEN_GRID_HOP_SAMPLES * channels * sizeof(int16_t);
}

void svc_report_stream_push(uint32_t seq, const int16_t *raw, const int16_t *clean)
{
    const uint8_t mode = atomic_load(&s_stream.mode);
    if (mode == GEN_STREAM_MODE_OFF || raw == NULL || (wants_clean(mode) && clean == NULL)) { return; }
    const size_t len = build_frame(mode, seq, raw, clean);
    // Whole frames only, zero timeout: a frame without room is dropped and counted, never split
    // (KEHOACH 5.3).
    if (xStreamBufferSpacesAvailable(s_stream.sb) < len ||
        xStreamBufferSend(s_stream.sb, s_stream.out.bytes, len, 0) != len) {
        atomic_fetch_add(&s_stream.dropped, 1);
    }
}

uint32_t svc_report_stream_dropped(void)
{
    return atomic_load(&s_stream.dropped);
}

static void drain(void)
{
    while (xStreamBufferReceive(s_stream.sb, s_stream.in.bytes, sizeof(s_stream.in.bytes), 0) > 0) {}
}

static bool read_exact(uint8_t *dst, size_t n)
{
    size_t got = 0;
    while (got < n) {
        const size_t chunk =
            xStreamBufferReceive(s_stream.sb, dst + got, n - got, pdMS_TO_TICKS(RECEIVE_WAIT_MS));
        if (chunk == 0) { return false; }
        got += chunk;
    }
    return true;
}

// A frame in sb_stream is always whole, so reading its header then its payload never splits one.
static size_t receive_frame(void)
{
    if (!read_exact(s_stream.in.bytes, GEN_STREAM_HEADER_BYTES)) { return 0; }
    gen_stream_header_t header;
    memcpy(&header, s_stream.in.bytes, sizeof(header));
    const size_t payload = (size_t)header.samples * header.channels * sizeof(int16_t);
    if (header.magic != GEN_STREAM_MAGIC || GEN_STREAM_HEADER_BYTES + payload > sizeof(s_stream.in.bytes) ||
        !read_exact(s_stream.in.bytes + GEN_STREAM_HEADER_BYTES, payload)) {
        drain();
        return 0;
    }
    return GEN_STREAM_HEADER_BYTES + payload;
}

static bool connect_when_due(uint8_t mode)
{
    const int64_t now = esp_timer_get_time();
    if (now < s_stream.next_connect_us) {
        vTaskDelay(pdMS_TO_TICKS(RECEIVE_WAIT_MS));
        return false;
    }
    char host[HOST_BYTES];
    taskENTER_CRITICAL(&s_stream.lock);
    strlcpy(host, s_stream.host, sizeof(host));
    const uint16_t port = s_stream.port;
    taskEXIT_CRITICAL(&s_stream.lock);
    const esp_err_t err = net_stream_connect(host, port, CONFIG_NET_STREAM_CONNECT_TIMEOUT_MS);
    if (err != ESP_OK) {
        s_stream.next_connect_us = now + RECONNECT_BACKOFF_US;
        if (!s_stream.told_failure) {
            ESP_LOGW(TAG, "stream to %s:%u: %s", host, (unsigned)port, esp_err_to_name(err));
        }
        s_stream.told_failure = true;
        return false;
    }
    ESP_LOGI(TAG, "stream connected to %s:%u, mode %u", host, (unsigned)port, (unsigned)mode);
    s_stream.told_failure = false;
    s_stream.sent_mode = mode;
    return true;
}

static bool expired(void)
{
    taskENTER_CRITICAL(&s_stream.lock);
    const int64_t until_us = s_stream.until_us;
    taskEXIT_CRITICAL(&s_stream.lock);
    return esp_timer_get_time() >= until_us;
}

void svc_report_luong_step(void)
{
    uint8_t mode = atomic_load(&s_stream.mode);
    if (mode != GEN_STREAM_MODE_OFF && expired()) {
        ESP_LOGI(TAG, "stream closed at its deadline");
        svc_report_stream_stop();
        mode = GEN_STREAM_MODE_OFF;
    }
    if (mode == GEN_STREAM_MODE_OFF || (net_stream_is_connected() && mode != s_stream.sent_mode)) {
        net_stream_close();
        drain();
        if (mode == GEN_STREAM_MODE_OFF) {
            xEventGroupWaitBits(s_stream.system, APP_BIT_STREAM_ON, pdFALSE, pdTRUE,
                                pdMS_TO_TICKS(IDLE_WAIT_MS));
        }
        return;
    }
    if (!net_stream_is_connected() && !connect_when_due(mode)) { return; }
    const size_t len = receive_frame();
    if (len > 0 && net_stream_send(s_stream.in.bytes, len, CONFIG_NET_STREAM_SEND_TIMEOUT_MS) != ESP_OK) {
        ESP_LOGW(TAG, "stream send failed, reconnecting");
        s_stream.next_connect_us = esp_timer_get_time() + RECONNECT_BACKOFF_US;
    }
}
