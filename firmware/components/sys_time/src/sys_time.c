#include "sys_time.h"

#include <inttypes.h>
#include <stdlib.h>
#include <sys/time.h>
#include <time.h>

#include "esp_attr.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "gen_vision.h"

#define US_PER_S 1000000LL
#define US_PER_MS 1000
#define WINDOW_US (GEN_VISION_CLOCK_WINDOW_S * US_PER_S)
#define TEXT_BYTES 32

static const char *TAG = "sys_time";

static EXT_RAM_BSS_ATTR struct {
    bool ready;
    bool set;
    bool has_best;
    int64_t offset_us; // UTC minus esp_timer
    int64_t best_us;   // the largest offer of the open window
    int64_t window_us; // esp_timer when the open window began
} s_clock;
static portMUX_TYPE s_clock_lock = portMUX_INITIALIZER_UNLOCKED;

esp_err_t sys_time_init(const char *tz)
{
    if (tz == NULL || tz[0] == '\0') { return ESP_ERR_INVALID_ARG; }
    if (s_clock.ready) { return ESP_ERR_INVALID_STATE; }
    if (setenv("TZ", tz, 1) != 0) { return ESP_ERR_NO_MEM; }
    tzset();
    s_clock.ready = true;
    return ESP_OK;
}

static void set_system_time(int64_t offset_us)
{
    const int64_t now_us = offset_us + esp_timer_get_time();
    const struct timeval now = {.tv_sec = (time_t)(now_us / US_PER_S),
                                .tv_usec = (suseconds_t)(now_us % US_PER_S)};
    settimeofday(&now, NULL);
}

static void log_first(void)
{
    const time_t now = time(NULL);
    struct tm utc;
    char text[TEXT_BYTES];
    gmtime_r(&now, &utc);
    strftime(text, sizeof(text), "%Y-%m-%d %H:%M:%S", &utc);
    ESP_LOGI(TAG, "clock set from GPS: %s UTC", text);
}

void sys_time_offer(uint32_t utc_s, int64_t read_us)
{
    // A reading lags its RMC, so each offer undershoots the true offset: the largest is the closest.
    const int64_t offer_us = (int64_t)utc_s * US_PER_S - read_us;
    taskENTER_CRITICAL(&s_clock_lock);
    if (!s_clock.has_best || offer_us > s_clock.best_us) {
        s_clock.best_us = offer_us;
        s_clock.has_best = true;
    }
    const bool first = !s_clock.set;
    const bool adopt = first || read_us - s_clock.window_us >= WINDOW_US;
    const int64_t moved_us = s_clock.best_us - s_clock.offset_us;
    if (adopt) {
        s_clock.offset_us = s_clock.best_us;
        s_clock.set = true;
        s_clock.has_best = false;
        s_clock.window_us = read_us;
    }
    const int64_t offset_us = s_clock.offset_us;
    taskEXIT_CRITICAL(&s_clock_lock);
    if (!adopt) { return; }
    set_system_time(offset_us);
    if (first) {
        log_first();
    } else {
        ESP_LOGI(TAG, "clock moved %+" PRId64 " ms", moved_us / US_PER_MS);
    }
}

bool sys_time_utc_ms(int64_t uptime_us, int64_t *utc_ms)
{
    taskENTER_CRITICAL(&s_clock_lock);
    const bool set = s_clock.set;
    const int64_t offset_us = s_clock.offset_us;
    taskEXIT_CRITICAL(&s_clock_lock);
    if (set) { *utc_ms = (offset_us + uptime_us) / US_PER_MS; }
    return set;
}
