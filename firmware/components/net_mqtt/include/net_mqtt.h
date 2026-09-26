/** MQTT to the broker of KEHOACH 7.3: retained status with an offline will, and the up payloads.
 *  @ctx task | non-blocking once started | esp-mqtt owns its task; cJSON allocates from boot arenas
 */
#pragma once

#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "gen_payload.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    const char *device_id;     // copied; also the client id and default username
    const char *fw_version;    // copied into status
    EventGroupHandle_t system; // APP_BIT_MQTT_OK follows the session
} net_mqtt_config_t;

typedef struct {
    uint32_t connects;
    uint32_t disconnects;
    uint32_t publish_failures; // enqueue refused or payload too large
    uint32_t json_alloc_failures;
    uint16_t json_arena_peak; // bytes, highest of the two arenas
} net_mqtt_stats_t;

/** Take the JSON arenas from PSRAM and point cJSON at them (FREERTOS.md 14 P1).
 *  @ctx task | blocking briefly | once at boot, ahead of any cJSON call anywhere in the firmware
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE on a second call | ESP_ERR_NO_MEM
 */
esp_err_t net_mqtt_init(void);

/** Connect with NVS device/mqtt_uri (else NET_MQTT_URI_FALLBACK), mqtt_user (else deviceId) and mqtt_pass.
 *  @ctx task | non-blocking | once after Wi-Fi is up; esp-mqtt reconnects on its own from then on
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND no URI | ESP_ERR_NOT_SUPPORTED not mqtts under REQUIRE_TLS | ESP_FAIL
 */
esp_err_t net_mqtt_start(const net_mqtt_config_t *cfg);

/** Queue one heartbeat; deviceId and jsonArenaPeak are filled here.
 *  @ctx task | non-blocking, never waits on the network | caller keeps hb
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no session | ESP_ERR_NO_MEM arena or outbox full
 */
esp_err_t net_mqtt_publish_heartbeat(heartbeat_t *hb);

/** Session counters and arena peak.
 *  @ctx any | non-blocking
 */
void net_mqtt_stats(net_mqtt_stats_t *out);

#ifdef __cplusplus
}
#endif
