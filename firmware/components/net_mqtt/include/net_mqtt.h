/** MQTT to the broker of KEHOACH 7.3: retained status with an offline will, the up payloads, and parsed into
 *  their queues (KEHOACH 5.3) the command sets of down/commands, the instructions of down/cmd and the rosters
 * of down/speakers.
 *  @ctx task | non-blocking once started | esp-mqtt owns its task; cJSON allocates from boot arenas
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "app_events.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "gen_payload.h"
#include "gen_topics.h"

#ifdef __cplusplus
extern "C" {
#endif

#define NET_MQTT_COMMANDS_TEXT_BYTES 32768 // command_set JSON: 64 commands of 512 bytes
#define NET_MQTT_CMD_TEXT_BYTES 1024       // device_cmd JSON: SPEAK's 128 characters, every field
#define NET_MQTT_SPEAKERS_TEXT_BYTES 2048  // speaker_set JSON: 8 names of 31 escaped characters

typedef struct {
    const char *device_id;     // copied; also the client id and default username
    const char *fw_version;    // copied into status
    EventGroupHandle_t system; // APP_BIT_MQTT_OK follows the session
    QueueHandle_t cmdset;      // q_cmdset, net_mqtt_commands_t *; NULL: not subscribed
    QueueHandle_t cmd;         // q_cmd, device_cmd_t; NULL: not subscribed
    QueueHandle_t spkset;      // q_spkset, net_mqtt_speakers_t, depth 1, or NULL
} net_mqtt_config_t;

typedef struct {
    esp_err_t parsed;  // ESP_OK | APP_ERR_COMMANDS_INVALID | ESP_ERR_NO_MEM
    command_set_t set; // valid when parsed is ESP_OK
    size_t text_bytes; // of text, the payload as it came, for set.json
    char text[NET_MQTT_COMMANDS_TEXT_BYTES];
} net_mqtt_commands_t;

typedef struct {
    esp_err_t parsed;      // ESP_OK | APP_ERR_SPEAKERS_INVALID | ESP_ERR_NO_MEM
    app_speaker_set_t set; // valid when parsed is ESP_OK
} net_mqtt_speakers_t;

typedef esp_err_t (*net_mqtt_fill_t)(const void *ctx, size_t offset, uint8_t *data, size_t bytes);

typedef struct {
    gen_topic_id_t topic; // GEN_TOPIC_IMAGE or GEN_TOPIC_NOTE
    uint16_t flags;       // GEN_VISION_CHUNK_FLAG_*
    uint32_t boot;
    uint32_t seq;
    size_t total_bytes;
    net_mqtt_fill_t fill; // the payload's bytes from offset on into data
    const void *ctx;      // fill's
} net_mqtt_chunks_t;

typedef struct {
    uint32_t connects;
    uint32_t disconnects;
    uint32_t publish_failures; // enqueue refused or payload too large
    uint32_t cmd_dropped;      // q_cmd full, or a down/cmd outside the contract
    uint32_t json_alloc_failures;
    uint32_t json_arena_peak; // bytes, highest of the two arenas
} net_mqtt_stats_t;

/** Take the JSON arenas and the two command-set buffers from PSRAM and point cJSON at the arenas.
 *  @ctx task | blocking briefly | once at boot, ahead of any cJSON call anywhere in the firmware
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE on a second call | ESP_ERR_NO_MEM
 */
esp_err_t net_mqtt_init(void);

/** Connect with NVS device/mqtt_uri (else NET_MQTT_URI_FALLBACK), mqtt_user (else deviceId) and mqtt_pass.
 *  A down/commands replaces a set waiting in cmdset, one taken stays untouched until the next (FREERTOS.md
 *  8.10); down/cmd goes to cmd without waiting; down/speakers overwrites spkset; empty payloads go nowhere.
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

/** Queue the speaker roster on up/speakers, retained; deviceId is filled here.
 *  @ctx task | non-blocking, never waits on the network | caller keeps roster
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no session | ESP_ERR_NO_MEM arena or outbox full
 */
esp_err_t net_mqtt_publish_roster(speaker_roster_t *roster);

/** Send what detect found in one capture now, without waiting for mqtt_task; deviceId is filled here.
 *  @ctx nhin_task | blocking on the network | caller keeps found
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no session | ESP_ERR_NO_MEM arena full or the send failed
 */
esp_err_t net_mqtt_publish_detections(detections_t *found);

/** Queue one report of board C's peripherals for mqtt_task; deviceId is filled here.
 *  @ctx do_task | non-blocking | caller keeps report
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no session | ESP_ERR_NO_MEM arena full or the outbox refused it
 */
esp_err_t net_mqtt_publish_sensors(sensors_t *report);

/** Take the one chunk buffer of pictures and voice notes from PSRAM, for net_mqtt_publish_chunks.
 *  @ctx app_boot | blocking briefly | once, only with a camera
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE a second call | ESP_ERR_NO_MEM
 */
esp_err_t net_mqtt_chunks_init(void);

/** Send a picture, preview frame or voice note in chunks of contracts/vision.yaml, headed and checked here.
 *  @ctx nhin_task alone: one buffer | blocking: a preview now at QoS 0, others on an outbox over 128 KB
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no session or buffer | ESP_ERR_NO_MEM outbox full | ESP_ERR_TIMEOUT
 * never drained | ESP_FAIL a preview chunk lost | the error fill returned
 */
esp_err_t net_mqtt_publish_chunks(const net_mqtt_chunks_t *chunks);

/** Queue one journal entry for mqtt_task; deviceId is filled here.
 *  @ctx nhin_task | non-blocking | caller keeps entry
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no session | ESP_ERR_NO_MEM arena full or the outbox refused it
 */
esp_err_t net_mqtt_publish_journal(journal_t *entry);

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
