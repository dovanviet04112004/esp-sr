/** Station-mode Wi-Fi that rejoins forever with a 1 to 30 s back-off (KEHOACH 7.2).
 *  @ctx task | non-blocking once up | retries run in sys_evt and esp_timer callbacks, no task of its own
 */
#pragma once

#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t connects; // addresses obtained
    uint32_t disconnects;
    uint16_t last_reason; // wifi_err_reason_t of the latest drop, 0 until one
    uint32_t backoff_ms;  // wait ahead of the next attempt
    int8_t rssi_dbm;      // 0 when not associated
} net_wifi_stats_t;

/** Create netif, the default event loop and the driver in station mode; credentials stay in RAM.
 *  @ctx task | blocking ~100 ms | once; APP_BIT_WIFI_OK of system follows the link from then on
 *  @param hostname DHCP host name, the deviceId; NULL keeps the IDF default
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG system NULL | ESP_ERR_INVALID_STATE on a second call | an esp_wifi error
 */
esp_err_t net_wifi_init(EventGroupHandle_t system, const char *hostname);

/** Load NVS wifi/ssid and wifi/pass into the driver and start joining; returns without waiting for a link.
 *  @ctx task | blocking briefly, reads NVS | after net_wifi_init; a second call rejoins with fresh values
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND no wifi/ssid | ESP_ERR_INVALID_STATE init not done | an esp_wifi error
 */
esp_err_t net_wifi_apply(void);

/** Link counters and signal level, for heartbeat.
 *  @ctx task | non-blocking
 */
void net_wifi_stats(net_wifi_stats_t *out);

#ifdef __cplusplus
}
#endif
