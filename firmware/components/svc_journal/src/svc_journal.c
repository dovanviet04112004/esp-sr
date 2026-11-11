#include "svc_journal.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>

#include "app_events.h"
#include "drv_sd.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "gen_payload.h"
#include "net_mqtt.h"
#include "svc_note.h"
#include "svc_vision.h"

#define PATH_BYTES 32

static const char *TAG = "svc_journal";

static svc_journal_config_t s_cfg;
static sensors_t *s_report;

esp_err_t svc_journal_init(const svc_journal_config_t *cfg)
{
    if (cfg == NULL) { return ESP_ERR_INVALID_ARG; }
    s_cfg = *cfg;
    s_report = heap_caps_malloc(sizeof(*s_report), MALLOC_CAP_SPIRAM);
    return s_report != NULL ? ESP_OK : ESP_ERR_NO_MEM;
}

static void beep(app_tone_t tone)
{
    if (s_cfg.speak == NULL) { return; }
    const app_speak_req_t request = {.tone = tone};
    if (xQueueSend(s_cfg.speak, &request, 0) != pdTRUE) {
        ESP_LOGW(TAG, "q_speak full, tone %d lost", (int)tone);
    }
}

static esp_err_t write_card(void *ctx, const char *text, size_t bytes)
{
    char path[PATH_BYTES];
    esp_err_t err = drv_sd_name(ctx, "JSN", path, sizeof(path));
    if (err == ESP_OK) { err = drv_sd_write(path, (const uint8_t *)text, bytes); }
    return err;
}

static bool keep_on_card(uint32_t seq, journal_t *entry, detections_t *found)
{
    if (s_report == NULL || !drv_sd_ready()) { return false; }
    sensors_t *report = s_cfg.sensors != NULL && s_cfg.sensors(s_report) ? s_report : NULL;
    char stem[PATH_BYTES];
    snprintf(stem, sizeof(stem), "IMG/B%" PRIu32 "/%06" PRIu32, s_cfg.boot, seq);
    const esp_err_t err = net_mqtt_print_entry(entry, found, report, write_card, stem);
    if (err != ESP_OK) { ESP_LOGW(TAG, "%s.JSN not kept: %s", stem, esp_err_to_name(err)); }
    return err == ESP_OK;
}

void svc_journal_keep(uint32_t seq)
{
    // The note and the pictures at once: the picture shows the scene of the command (KEHOACH 3.19).
    const esp_err_t asked = svc_note_start(seq);
    if (asked != ESP_OK) {
        ESP_LOGW(TAG, "B%" PRIu32 "/%06" PRIu32 ": no note: %s", s_cfg.boot, seq, esp_err_to_name(asked));
    }
    detections_t *found = svc_vision_detect(seq);
    const svc_vision_full_t full = svc_vision_full(seq);
    svc_note_t note = {.seq = seq};
    const esp_err_t heard = asked == ESP_OK ? svc_note_wait(seq, &note) : asked;
    if (heard != ESP_OK && asked == ESP_OK) { ESP_LOGW(TAG, "note: %s", esp_err_to_name(heard)); }
    const bool note_sent = note.saved && svc_note_send() == ESP_OK;
    journal_t entry = {
        .boot = s_cfg.boot,
        .capture_seq = seq,
        .note_ms = note.ms,
        .note_bytes = note.bytes,
        .note_dropped = note.dropped,
        .note_saved = note.saved,
        .image_bytes = full.bytes,
        .saved = full.saved,
    };
    const esp_err_t told = net_mqtt_publish_journal(&entry);
    if (told != ESP_OK) { ESP_LOGW(TAG, "journal not sent: %s", esp_err_to_name(told)); }
    const bool carded = keep_on_card(seq, &entry, found);
    beep(note.saved && (full.saved || full.sent) ? APP_TONE_DONE : APP_TONE_FAIL);
    ESP_LOGI(TAG, "B%" PRIu32 "/%06" PRIu32 ": note %s, picture %s, card %s", s_cfg.boot, seq,
             note_sent ? "sent" : "not sent", full.sent ? "sent" : "not sent", carded ? "kept" : "not kept");
}
