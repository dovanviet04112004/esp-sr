/** MQTT to the broker of KEHOACH 7.3: retained status with an offline will, the up payloads, and the command
 *  sets of down/commands parsed into q_cmdset (KEHOACH 5.3).
 *  @ctx task | non-blocking once started | esp-mqtt owns its task; cJSON allocates from boot arenas
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "gen_payload.h"

#ifdef __cplusplus
extern "C" {
#endif

#define NET_MQTT_COMMANDS_TEXT_BYTES 154112 // command_set JSON: 301 commands of 512 bytes

typedef struct {
    const char *device_id;     // copied; also the client id and default username
    const char *fw_version;    // copied into status
    EventGroupHandle_t system; // APP_BIT_MQTT_OK follows the session
    QueueHandle_t cmdset;      // q_cmdset, net_mqtt_commands_t *; NULL: not subscribed
} net_mqtt_config_t;

typedef struct {
    esp_err_t parsed;  // ESP_OK | APP_ERR_COMMANDS_INVALID | ESP_ERR_NO_MEM
    command_set_t set; // valid when parsed is ESP_OK
    size_t text_bytes; // of text, the payload as it came, for set.json
    char text[NET_MQTT_COMMANDS_TEXT_BYTES];
} net_mqtt_commands_t;

typedef struct {
    uint32_t connects;
    uint32_t disconnects;
    uint32_t publish_failures; // enqueue refused or payload too large
    uint32_t json_alloc_failures;
    uint32_t json_arena_peak; // bytes, highest of the two arenas
} net_mqtt_stats_t;

/** Take the JSON arenas and the two command-set buffers from PSRAM and point cJSON at the arenas.
 *  @ctx task | blocking briefly | once at boot, ahead of any cJSON call anywhere in the firmware
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE on a second call | ESP_ERR_NO_MEM
 */
esp_err_t net_mqtt_init(void);

/** Connect with NVS device/mqtt_uri (else NET_MQTT_URI_FALLBACK), mqtt_user (else deviceId) and mqtt_pass.
 *  Each non-empty down/commands goes to cmdset, replacing a set still waiting there; a set taken from cmdset
 *  stays untouched until its receiver takes the next one (FREERTOS.md 8.10).
 *  @ctx task | non-blocking | once after Wi-Fi is up; esp-mqtt reconnects and subscribes again on its own
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND no URI | ESP_ERR_NOT_SUPPORTED not mqtts under REQUIRE_TLS | ESP_FAIL
 */
esp_err_t net_mqtt_start(const net_mqtt_config_t *cfg);

/** Queue one heartbeat; deviceId and jsonArenaPeak are filled here.
 *  @ctx task | non-blocking, never waits on the network | caller keeps hb
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no session | ESP_ERR_NO_MEM arena or outbox full
 */
esp_err_t net_mqtt_publish_heartbeat(heartbeat_t *hb);

/** Queue one event of the listener; deviceId is filled here.
 *  @ctx task | non-blocking, never waits on the network | caller keeps ev
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no session | ESP_ERR_NO_MEM arena or outbox full
 */
esp_err_t net_mqtt_publish_event(event_t *ev);

/** Parse a command set laid out as contracts/schema/command_set.schema.json, through the caller's arena.
 *  @ctx task | non-blocking | text need not end in NUL; out belongs to the caller, about 20 KB: not the stack
 *  @ret ESP_OK | APP_ERR_COMMANDS_INVALID not JSON or outside the schema | ESP_ERR_NO_MEM arena full
 */
esp_err_t net_mqtt_parse_command_set(const char *text, size_t len, command_set_t *out);

/** Session counters and arena peak.
 *  @ctx any | non-blocking
 */
void net_mqtt_stats(net_mqtt_stats_t *out);

#ifdef __cplusplus
}
#endif
