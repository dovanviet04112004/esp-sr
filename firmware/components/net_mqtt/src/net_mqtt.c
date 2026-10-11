#include "net_mqtt.h"

#include <stdbool.h>
#include <string.h>

#include "app_err.h"
#include "app_events.h"
#include "cJSON.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/task.h"
#include "gen_topics.h"
#include "mqtt_client.h"
#include "sdkconfig.h"
#include "sys_storage.h"

#define JSON_ARENAS 2
#define JSON_ALIGN 8     // cJSON nodes hold doubles
#define PRINT_BYTES 2048 // a roster of 8 names of 31 escaped characters
#define STATUS_BYTES 160
#define URI_BYTES 128
#define USER_BYTES 65
#define PASS_BYTES 65
#define TLS_SCHEME "mqtts://"
#define COMMANDS_SLOTS 2 // the set its receiver reads, and one filling or waiting

_Static_assert(sizeof(((speaker_set_t *)0)->speakers) / sizeof(((speaker_set_t *)0)->speakers[0]) ==
                       APP_SPEAKERS_MAX &&
                   sizeof(((speaker_set_speakers_item_t *)0)->id) == APP_SPEAKER_ID_BYTES &&
                   sizeof(((speaker_set_speakers_item_t *)0)->name) == APP_SPEAKER_NAME_BYTES,
               "app_speaker_set_t holds what speaker_set.schema.json allows");

typedef struct {
    char *text;
    size_t cap, bytes;
    bool fits;
} rx_text_t;

typedef struct {
    TaskHandle_t owner;
    uint8_t *base;
    size_t used;
} json_arena_t;

static const char *TAG = "net_mqtt";

static json_arena_t s_arena[JSON_ARENAS];
static portMUX_TYPE s_arena_lock = portMUX_INITIALIZER_UNLOCKED;
static size_t s_arena_peak;
static bool s_arena_ready;

static esp_mqtt_client_handle_t s_client;
static EventGroupHandle_t s_system;
static volatile bool s_connected;
static char s_device_id[GEN_TOPIC_DEVICE_ID_MAX + 1];
static char s_fw[sizeof(((status_t *)NULL)->fw)];
static char s_status_topic[GEN_TOPIC_MAX_LEN + 1];
static char s_will[STATUS_BYTES];
static net_mqtt_stats_t s_stats;

static QueueHandle_t s_cmdset, s_cmd, s_spkset;
static net_mqtt_commands_t *s_commands;    // COMMANDS_SLOTS in PSRAM
static net_mqtt_commands_t *s_filling;     // esp-mqtt task only: the slot a payload goes into
static net_mqtt_commands_t *s_sent;        // esp-mqtt task only: the slot queued last
static gen_topic_id_t s_rx;                // esp-mqtt task only: topic of the data events in flight
static rx_text_t s_rx_text;                // esp-mqtt task only: where their pieces go
static char *s_cmd_text, *s_speakers_text; // PSRAM, esp-mqtt task only
static device_cmd_t *s_cmd_in;             // PSRAM, esp-mqtt task only
static speaker_set_t *s_set_in;            // PSRAM, esp-mqtt task only
static net_mqtt_speakers_t *s_speakers_in; // PSRAM, esp-mqtt task only

// A task owns an arena from its first allocation until json_end, so two tasks never share one.
static json_arena_t *arena_of_caller(bool claim)
{
    const TaskHandle_t self = xTaskGetCurrentTaskHandle();
    json_arena_t *found = NULL;
    taskENTER_CRITICAL(&s_arena_lock);
    for (size_t i = 0; i < JSON_ARENAS && found == NULL; i++) {
        if (s_arena[i].owner == self) { found = &s_arena[i]; }
    }
    for (size_t i = 0; i < JSON_ARENAS && found == NULL && claim; i++) {
        if (s_arena[i].owner == NULL) {
            s_arena[i].owner = self;
            found = &s_arena[i];
        }
    }
    taskEXIT_CRITICAL(&s_arena_lock);
    return found;
}

static void *json_malloc(size_t bytes)
{
    json_arena_t *arena = arena_of_caller(true);
    const size_t need = (bytes + JSON_ALIGN - 1) & ~(size_t)(JSON_ALIGN - 1);
    if (arena == NULL || arena->used + need > CONFIG_NET_MQTT_JSON_ARENA_BYTES) {
        s_stats.json_alloc_failures++;
        return NULL;
    }
    void *block = arena->base + arena->used;
    arena->used += need;
    if (arena->used > s_arena_peak) { s_arena_peak = arena->used; }
    return block;
}

static void json_free(void *block)
{
    (void)block;
}

static void json_end(void)
{
    json_arena_t *arena = arena_of_caller(false);
    if (arena == NULL) { return; }
    taskENTER_CRITICAL(&s_arena_lock);
    arena->used = 0;
    arena->owner = NULL;
    taskEXIT_CRITICAL(&s_arena_lock);
}

esp_err_t net_mqtt_init(void)
{
    if (s_arena_ready) { return ESP_ERR_INVALID_STATE; }
    uint8_t *region =
        heap_caps_malloc((size_t)JSON_ARENAS * CONFIG_NET_MQTT_JSON_ARENA_BYTES, MALLOC_CAP_SPIRAM);
    s_commands = heap_caps_calloc(COMMANDS_SLOTS, sizeof(net_mqtt_commands_t), MALLOC_CAP_SPIRAM);
    s_cmd_text = heap_caps_malloc(NET_MQTT_CMD_TEXT_BYTES, MALLOC_CAP_SPIRAM);
    s_speakers_text = heap_caps_malloc(NET_MQTT_SPEAKERS_TEXT_BYTES, MALLOC_CAP_SPIRAM);
    s_cmd_in = heap_caps_malloc(sizeof(*s_cmd_in), MALLOC_CAP_SPIRAM);
    s_set_in = heap_caps_malloc(sizeof(*s_set_in), MALLOC_CAP_SPIRAM);
    s_speakers_in = heap_caps_malloc(sizeof(*s_speakers_in), MALLOC_CAP_SPIRAM);
    if (region == NULL || s_commands == NULL || s_cmd_text == NULL || s_speakers_text == NULL ||
        s_cmd_in == NULL || s_set_in == NULL || s_speakers_in == NULL) {
        void *const blocks[] = {region,   s_commands, s_cmd_text,   s_speakers_text,
                                s_cmd_in, s_set_in,   s_speakers_in};
        for (size_t i = 0; i < sizeof(blocks) / sizeof(blocks[0]); i++) {
            heap_caps_free(blocks[i]);
        }
        s_commands = NULL;
        return ESP_ERR_NO_MEM;
    }
    for (size_t i = 0; i < JSON_ARENAS; i++) {
        s_arena[i].base = region + i * CONFIG_NET_MQTT_JSON_ARENA_BYTES;
    }
    cJSON_Hooks hooks = {.malloc_fn = json_malloc, .free_fn = json_free};
    cJSON_InitHooks(&hooks);
    s_arena_ready = true;
    return ESP_OK;
}

static esp_err_t status_json(status_state_t state, char *out, size_t cap)
{
    status_t status = {.state = state, .has_fw = true};
    strlcpy(status.device_id, s_device_id, sizeof(status.device_id));
    strlcpy(status.fw, s_fw, sizeof(status.fw));
    cJSON *root = status_to_json(&status);
    const bool printed = root != NULL && cJSON_PrintPreallocated(root, out, (int)cap, false);
    cJSON_Delete(root);
    json_end();
    return printed ? ESP_OK : ESP_ERR_NO_MEM;
}

static esp_err_t send_payload(cJSON *root, gen_topic_id_t id)
{
    char topic[GEN_TOPIC_MAX_LEN + 1];
    char *text = root != NULL ? json_malloc(PRINT_BYTES) : NULL;
    esp_err_t err = ESP_ERR_NO_MEM;
    if (text != NULL && cJSON_PrintPreallocated(root, text, PRINT_BYTES, false) &&
        gen_topic_build(id, s_device_id, topic, sizeof(topic))) {
        const gen_topic_info_t *info = &GEN_TOPIC_INFO[id];
        err = esp_mqtt_client_enqueue(s_client, topic, text, 0, info->qos, info->retain, true) >= 0
                  ? ESP_OK
                  : ESP_ERR_NO_MEM;
    }
    if (err != ESP_OK) { s_stats.publish_failures++; }
    cJSON_Delete(root);
    json_end();
    return err;
}

static void subscribe(gen_topic_id_t id, QueueHandle_t into)
{
    char topic[GEN_TOPIC_MAX_LEN + 1];
    if (into == NULL || !gen_topic_build(id, s_device_id, topic, sizeof(topic))) { return; }
    if (esp_mqtt_client_subscribe_single(s_client, topic, GEN_TOPIC_INFO[id].qos) < 0) {
        ESP_LOGW(TAG, "subscribe to %s not sent", topic);
    }
}

static void on_connected(void)
{
    char online[STATUS_BYTES];
    s_stats.connects++;
    s_connected = true;
    if (status_json(STATUS_STATE_ONLINE, online, sizeof(online)) != ESP_OK ||
        esp_mqtt_client_enqueue(s_client, s_status_topic, online, 0, GEN_TOPIC_INFO[GEN_TOPIC_STATUS].qos,
                                GEN_TOPIC_INFO[GEN_TOPIC_STATUS].retain, true) < 0) {
        s_stats.publish_failures++;
    }
    subscribe(GEN_TOPIC_COMMANDS, s_cmdset);
    subscribe(GEN_TOPIC_CMD, s_cmd);
    subscribe(GEN_TOPIC_SPEAKERS, s_spkset);
    xEventGroupSetBits(s_system, APP_BIT_MQTT_OK);
    ESP_LOGI(TAG, "session up, %s ONLINE", s_status_topic);
}

// The topic of a payload worth taking, or GEN_TOPIC_NONE; an empty one is a retained copy cleared.
static gen_topic_id_t taken_topic(const esp_mqtt_event_handle_t e)
{
    if (e->topic == NULL || e->total_data_len <= 0) { return GEN_TOPIC_NONE; }
    const gen_topic_id_t id = gen_topic_match(e->topic, (size_t)e->topic_len, s_device_id);
    const bool wanted = (id == GEN_TOPIC_COMMANDS && s_cmdset != NULL) ||
                        (id == GEN_TOPIC_CMD && s_cmd != NULL) ||
                        (id == GEN_TOPIC_SPEAKERS && s_spkset != NULL);
    return wanted ? id : GEN_TOPIC_NONE;
}

// A newer set takes back the one still waiting; the receiver keeps the one it took until it takes the next.
static net_mqtt_commands_t *commands_slot(void)
{
    net_mqtt_commands_t *waiting = NULL;
    if (s_filling != NULL) { return s_filling; }
    if (xQueueReceive(s_cmdset, &waiting, 0) == pdTRUE) { return waiting; }
    return s_sent == &s_commands[0] ? &s_commands[1] : &s_commands[0];
}

static void take_commands(void)
{
    net_mqtt_commands_t *slot = s_filling;
    slot->text_bytes = s_rx_text.bytes;
    slot->parsed = s_rx_text.fits ? net_mqtt_parse_command_set(slot->text, slot->text_bytes, &slot->set)
                                  : APP_ERR_COMMANDS_INVALID;
    s_filling = NULL;
    s_sent = slot;
    xQueueOverwrite(s_cmdset, &slot);
}

// A full q_cmd drops the instruction: waiting here stalls every topic (KEHOACH 5.3).
static void take_cmd(void)
{
    cJSON *root = s_rx_text.fits ? cJSON_ParseWithLength(s_rx_text.text, s_rx_text.bytes) : NULL;
    const bool valid = root != NULL && device_cmd_from_json(root, s_cmd_in);
    cJSON_Delete(root);
    json_end();
    if (!valid || xQueueSend(s_cmd, s_cmd_in, 0) != pdTRUE) {
        s_stats.cmd_dropped++;
        ESP_LOGW(TAG, "down/cmd dropped: %s", valid ? "q_cmd full" : "outside device_cmd.schema.json");
    }
}

static void to_app_set(const speaker_set_t *in, app_speaker_set_t *out)
{
    memset(out, 0, sizeof(*out));
    out->version = in->version;
    out->check = in->check;
    out->count = in->speakers_count;
    for (uint8_t i = 0; i < in->speakers_count; i++) {
        strlcpy(out->speakers[i].id, in->speakers[i].id, sizeof(out->speakers[i].id));
        strlcpy(out->speakers[i].name, in->speakers[i].name, sizeof(out->speakers[i].name));
    }
}

static void take_speakers(void)
{
    const uint32_t failures = s_stats.json_alloc_failures;
    cJSON *root = s_rx_text.fits ? cJSON_ParseWithLength(s_rx_text.text, s_rx_text.bytes) : NULL;
    const bool valid = root != NULL && speaker_set_from_json(root, s_set_in);
    cJSON_Delete(root);
    json_end();
    s_speakers_in->parsed = valid                                     ? ESP_OK
                            : s_stats.json_alloc_failures != failures ? ESP_ERR_NO_MEM
                                                                      : APP_ERR_SPEAKERS_INVALID;
    if (valid) { to_app_set(s_set_in, &s_speakers_in->set); }
    xQueueOverwrite(s_spkset, s_speakers_in);
}

static void begin_rx(const esp_mqtt_event_handle_t e)
{
    s_rx = taken_topic(e);
    switch (s_rx) {
    case GEN_TOPIC_COMMANDS:
        s_filling = commands_slot();
        s_rx_text = (rx_text_t){.text = s_filling->text, .cap = NET_MQTT_COMMANDS_TEXT_BYTES};
        break;
    case GEN_TOPIC_CMD: s_rx_text = (rx_text_t){.text = s_cmd_text, .cap = NET_MQTT_CMD_TEXT_BYTES}; break;
    case GEN_TOPIC_SPEAKERS:
        s_rx_text = (rx_text_t){.text = s_speakers_text, .cap = NET_MQTT_SPEAKERS_TEXT_BYTES};
        break;
    default: return;
    }
    s_rx_text.fits = (size_t)e->total_data_len <= s_rx_text.cap;
}

// esp-mqtt hands a payload longer than its buffer over in pieces; only the first names the topic.
static void on_data(const esp_mqtt_event_handle_t e)
{
    if (e->current_data_offset == 0) { begin_rx(e); }
    if (s_rx == GEN_TOPIC_NONE) { return; }
    const size_t piece = (size_t)e->data_len;
    if (s_rx_text.fits && (size_t)e->current_data_offset == s_rx_text.bytes &&
        s_rx_text.bytes + piece <= s_rx_text.cap) {
        memcpy(s_rx_text.text + s_rx_text.bytes, e->data, piece);
        s_rx_text.bytes += piece;
    } else {
        s_rx_text.fits = false;
    }
    if (e->current_data_offset + e->data_len < e->total_data_len) { return; }
    switch (s_rx) {
    case GEN_TOPIC_COMMANDS: take_commands(); break;
    case GEN_TOPIC_CMD: take_cmd(); break;
    default: take_speakers(); break;
    }
    s_rx = GEN_TOPIC_NONE;
}

static void on_mqtt_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    const esp_mqtt_event_handle_t event = data;
    switch ((esp_mqtt_event_id_t)id) {
    case MQTT_EVENT_CONNECTED: on_connected(); break;
    case MQTT_EVENT_DATA: on_data(event); break;
    case MQTT_EVENT_DISCONNECTED:
        s_connected = false;
        s_stats.disconnects++;
        xEventGroupClearBits(s_system, APP_BIT_MQTT_OK);
        ESP_LOGW(TAG, "session down");
        break;
    case MQTT_EVENT_ERROR:
        ESP_LOGW(TAG, "error type %d, connect rc %d, tls 0x%x", (int)event->error_handle->error_type,
                 (int)event->error_handle->connect_return_code,
                 (unsigned)event->error_handle->esp_tls_last_esp_err);
        break;
    default: break;
    }
}

esp_err_t net_mqtt_start(const net_mqtt_config_t *cfg)
{
    if (cfg == NULL || cfg->device_id == NULL || cfg->fw_version == NULL || cfg->system == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!s_arena_ready || s_client != NULL) { return ESP_ERR_INVALID_STATE; }
    char uri[URI_BYTES];
    if (sys_storage_get_str(STORAGE_NS_DEVICE, STORAGE_KEY_MQTT_URI, uri, sizeof(uri)) != ESP_OK) {
        strlcpy(uri, CONFIG_NET_MQTT_URI_FALLBACK, sizeof(uri));
    }
    if (uri[0] == '\0') { return ESP_ERR_NOT_FOUND; }
#if CONFIG_NET_MQTT_REQUIRE_TLS
    if (strncmp(uri, TLS_SCHEME, strlen(TLS_SCHEME)) != 0) { return ESP_ERR_NOT_SUPPORTED; }
#endif
    s_system = cfg->system;
    s_cmdset = cfg->cmdset;
    s_cmd = cfg->cmd;
    s_spkset = cfg->spkset;
    s_rx = GEN_TOPIC_NONE;
    strlcpy(s_device_id, cfg->device_id, sizeof(s_device_id));
    strlcpy(s_fw, cfg->fw_version, sizeof(s_fw));
    char user[USER_BYTES];
    if (sys_storage_get_str(STORAGE_NS_DEVICE, STORAGE_KEY_MQTT_USER, user, sizeof(user)) != ESP_OK) {
        strlcpy(user, s_device_id, sizeof(user));
    }
    char pass[PASS_BYTES];
    const bool has_pass =
        sys_storage_get_str(STORAGE_NS_DEVICE, STORAGE_KEY_MQTT_PASS, pass, sizeof(pass)) == ESP_OK;
    if (!gen_topic_status(s_device_id, s_status_topic, sizeof(s_status_topic)) ||
        status_json(STATUS_STATE_OFFLINE, s_will, sizeof(s_will)) != ESP_OK) {
        return ESP_ERR_INVALID_ARG;
    }
    const esp_mqtt_client_config_t mqtt = {
        .broker.address.uri = uri,
        .credentials.username = user,
        .credentials.client_id = s_device_id,
        .credentials.authentication.password = has_pass ? pass : NULL,
        .session.keepalive = CONFIG_NET_MQTT_KEEPALIVE_S,
        .session.last_will =
            {
                .topic = s_status_topic,
                .msg = s_will,
                .qos = GEN_TOPIC_INFO[GEN_TOPIC_STATUS].qos,
                .retain = GEN_TOPIC_INFO[GEN_TOPIC_STATUS].retain,
            },
    };
    s_client = esp_mqtt_client_init(&mqtt);
    memset(pass, 0, sizeof(pass));
    if (s_client == NULL) { return ESP_FAIL; }
    if (esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID, on_mqtt_event, NULL) != ESP_OK ||
        esp_mqtt_client_start(s_client) != ESP_OK) {
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "connecting to %s as %s%s", uri, user, has_pass ? " with a password" : "");
    return ESP_OK;
}

esp_err_t net_mqtt_publish_heartbeat(heartbeat_t *hb)
{
    if (hb == NULL) { return ESP_ERR_INVALID_ARG; }
    if (s_client == NULL || !s_connected) { return ESP_ERR_INVALID_STATE; }
    strlcpy(hb->device_id, s_device_id, sizeof(hb->device_id));
    hb->json_arena_peak = s_arena_peak;
    hb->has_json_arena_peak = true;
    return send_payload(heartbeat_to_json(hb), GEN_TOPIC_HEARTBEAT);
}

esp_err_t net_mqtt_publish_roster(speaker_roster_t *roster)
{
    if (roster == NULL) { return ESP_ERR_INVALID_ARG; }
    if (s_client == NULL || !s_connected) { return ESP_ERR_INVALID_STATE; }
    strlcpy(roster->device_id, s_device_id, sizeof(roster->device_id));
    return send_payload(speaker_roster_to_json(roster), GEN_TOPIC_ROSTER);
}

esp_err_t net_mqtt_parse_command_set(const char *text, size_t len, command_set_t *out)
{
    if (text == NULL || out == NULL) { return ESP_ERR_INVALID_ARG; }
    const uint32_t failures = s_stats.json_alloc_failures;
    cJSON *root = cJSON_ParseWithLength(text, len);
    const bool valid = root != NULL && command_set_from_json(root, out);
    cJSON_Delete(root);
    json_end();
    if (valid) { return ESP_OK; }
    return s_stats.json_alloc_failures != failures ? ESP_ERR_NO_MEM : APP_ERR_COMMANDS_INVALID;
}

esp_err_t net_mqtt_publish_event(event_t *ev)
{
    if (ev == NULL) { return ESP_ERR_INVALID_ARG; }
    if (s_client == NULL || !s_connected) { return ESP_ERR_INVALID_STATE; }
    strlcpy(ev->device_id, s_device_id, sizeof(ev->device_id));
    return send_payload(event_to_json(ev), GEN_TOPIC_EVENT);
}

void net_mqtt_stats(net_mqtt_stats_t *out)
{
    *out = s_stats;
    out->json_arena_peak = s_arena_peak;
}
