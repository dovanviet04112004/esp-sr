#include "net_wifi.h"

#include <stdbool.h>
#include <string.h>

#include "app_events.h"
#include "esp_check.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "sys_storage.h"

#define BACKOFF_MIN_MS 1000 // KEHOACH 7.2: 1 s doubling up to 30 s, never giving up
#define BACKOFF_MAX_MS 30000
#define US_PER_MS 1000

static const char *TAG = "net_wifi";

static EventGroupHandle_t s_system;
static esp_netif_t *s_netif;
static esp_timer_handle_t s_retry_timer;
static uint32_t s_backoff_ms = BACKOFF_MIN_MS;
static net_wifi_stats_t s_stats;
static bool s_ready;
static bool s_started;

static void join(void *arg)
{
    const esp_err_t err = esp_wifi_connect();
    if (err != ESP_OK) { ESP_LOGW(TAG, "connect: %s", esp_err_to_name(err)); }
}

static void retry_later(void)
{
    esp_timer_stop(s_retry_timer);
    esp_timer_start_once(s_retry_timer, (uint64_t)s_backoff_ms * US_PER_MS);
    s_backoff_ms = s_backoff_ms * 2 > BACKOFF_MAX_MS ? BACKOFF_MAX_MS : s_backoff_ms * 2;
}

static void on_wifi_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (id == WIFI_EVENT_STA_START) {
        join(NULL);
    } else if (id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *event = data;
        xEventGroupClearBits(s_system, APP_BIT_WIFI_OK);
        s_stats.disconnects++;
        s_stats.last_reason = event->reason;
        ESP_LOGW(TAG, "down, reason %u, next try in %u ms", (unsigned)event->reason, (unsigned)s_backoff_ms);
        retry_later();
    }
}

static void on_ip_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    if (id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = data;
        s_backoff_ms = BACKOFF_MIN_MS;
        s_stats.connects++;
        wifi_ap_record_t ap = {0};
        esp_wifi_sta_get_ap_info(&ap);
        ESP_LOGI(TAG, "up: " IPSTR ", rssi %d dBm, channel %u", IP2STR(&event->ip_info.ip), ap.rssi,
                 (unsigned)ap.primary);
        xEventGroupSetBits(s_system, APP_BIT_WIFI_OK);
    } else if (id == IP_EVENT_STA_LOST_IP) {
        xEventGroupClearBits(s_system, APP_BIT_WIFI_OK);
    }
}

esp_err_t net_wifi_init(EventGroupHandle_t system, const char *hostname)
{
    if (system == NULL) { return ESP_ERR_INVALID_ARG; }
    if (s_ready) { return ESP_ERR_INVALID_STATE; }
    s_system = system;
    ESP_RETURN_ON_ERROR(esp_netif_init(), TAG, "netif");
    const esp_err_t loop = esp_event_loop_create_default();
    if (loop != ESP_OK && loop != ESP_ERR_INVALID_STATE) { return loop; }
    s_netif = esp_netif_create_default_wifi_sta();
    if (s_netif == NULL) { return ESP_FAIL; }
    if (hostname != NULL) { ESP_RETURN_ON_ERROR(esp_netif_set_hostname(s_netif, hostname), TAG, "hostname"); }
    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    // The driver would otherwise keep its own copy of the credentials in NVS: only sys_storage writes NVS.
    cfg.nvs_enable = 0;
    ESP_RETURN_ON_ERROR(esp_wifi_init(&cfg), TAG, "wifi init");
    ESP_RETURN_ON_ERROR(
        esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_wifi_event, NULL, NULL), TAG,
        "wifi events");
    ESP_RETURN_ON_ERROR(
        esp_event_handler_instance_register(IP_EVENT, ESP_EVENT_ANY_ID, on_ip_event, NULL, NULL), TAG,
        "ip events");
    const esp_timer_create_args_t retry = {.callback = join, .name = "wifi_retry"};
    ESP_RETURN_ON_ERROR(esp_timer_create(&retry, &s_retry_timer), TAG, "timer");
    ESP_RETURN_ON_ERROR(esp_wifi_set_mode(WIFI_MODE_STA), TAG, "mode");
    ESP_RETURN_ON_ERROR(esp_wifi_set_ps(WIFI_PS_MIN_MODEM), TAG, "power save");
    s_ready = true;
    return ESP_OK;
}

esp_err_t net_wifi_apply(void)
{
    if (!s_ready) { return ESP_ERR_INVALID_STATE; }
    wifi_config_t cfg = {0};
    char ssid[sizeof(cfg.sta.ssid) + 1];
    char pass[sizeof(cfg.sta.password)];
    esp_err_t err = sys_storage_get_str(STORAGE_NS_WIFI, STORAGE_KEY_SSID, ssid, sizeof(ssid));
    if (err != ESP_OK) { return err; }
    err = sys_storage_get_str(STORAGE_NS_WIFI, STORAGE_KEY_PASS, pass, sizeof(pass));
    if (err != ESP_OK && err != ESP_ERR_NOT_FOUND) { return err; }
    memcpy(cfg.sta.ssid, ssid, strlen(ssid));
    memcpy(cfg.sta.password, pass, strlen(pass));
    cfg.sta.threshold.authmode = pass[0] != '\0' ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    err = esp_wifi_set_config(WIFI_IF_STA, &cfg);
    memset(pass, 0, sizeof(pass));
    memset(&cfg, 0, sizeof(cfg));
    ESP_RETURN_ON_ERROR(err, TAG, "config");
    s_backoff_ms = BACKOFF_MIN_MS;
    ESP_LOGI(TAG, "joining %s", ssid);
    if (!s_started) {
        ESP_RETURN_ON_ERROR(esp_wifi_start(), TAG, "start");
        s_started = true;
        return ESP_OK;
    }
    return esp_wifi_disconnect();
}

void net_wifi_stats(net_wifi_stats_t *out)
{
    *out = s_stats;
    out->backoff_ms = s_backoff_ms;
    wifi_ap_record_t ap = {0};
    out->rssi_dbm = esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;
}
