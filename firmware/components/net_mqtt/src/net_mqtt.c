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
#define JSON_ALIGN 8 // cJSON nodes hold doubles
#define PRINT_BYTES 1024
#define STATUS_BYTES 160
#define URI_BYTES 128
#define USER_BYTES 65
#define PASS_BYTES 65
#define TLS_SCHEME "mqtts://"

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
    if (region == NULL) { return ESP_ERR_NO_MEM; }
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
    xEventGroupSetBits(s_system, APP_BIT_MQTT_OK);
    ESP_LOGI(TAG, "session up, %s ONLINE", s_status_topic);
}

static void on_mqtt_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    const esp_mqtt_event_handle_t event = data;
    switch ((esp_mqtt_event_id_t)id) {
    case MQTT_EVENT_CONNECTED: on_connected(); break;
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
    hb->json_arena_peak = (uint16_t)s_arena_peak;
    hb->has_json_arena_peak = true;
    return send_payload(heartbeat_to_json(hb), GEN_TOPIC_HEARTBEAT);
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
    out->json_arena_peak = (uint16_t)s_arena_peak;
}
