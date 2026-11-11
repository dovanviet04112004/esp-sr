#include "svc_journal.h"

#include <inttypes.h>
#include <stdbool.h>

#include "app_events.h"
#include "esp_log.h"
#include "gen_payload.h"
#include "net_mqtt.h"
#include "svc_note.h"
#include "svc_vision.h"

static const char *TAG = "svc_journal";

static svc_journal_config_t s_cfg;

esp_err_t svc_journal_init(const svc_journal_config_t *cfg)
{
    if (cfg == NULL) { return ESP_ERR_INVALID_ARG; }
    s_cfg = *cfg;
    return ESP_OK;
}

static void beep(app_tone_t tone)
{
    if (s_cfg.speak == NULL) { return; }
    const app_speak_req_t request = {.tone = tone};
    if (xQueueSend(s_cfg.speak, &request, 0) != pdTRUE) {
        ESP_LOGW(TAG, "q_speak full, tone %d lost", (int)tone);
    }
}

void svc_journal_keep(uint32_t seq)
{
    svc_note_t note;
    svc_note_record(seq, &note);
    beep(APP_TONE_WAKE);
    svc_vision_detect(seq);
    const svc_vision_full_t full = svc_vision_full(seq);
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
    beep(note.saved && (full.saved || full.sent) ? APP_TONE_DONE : APP_TONE_FAIL);
    ESP_LOGI(TAG, "B%" PRIu32 "/%06" PRIu32 ": note %s, picture %s", s_cfg.boot, seq,
             note_sent ? "sent" : "not sent", full.sent ? "sent" : "not sent");
}
