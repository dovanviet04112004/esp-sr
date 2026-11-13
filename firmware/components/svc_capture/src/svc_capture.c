#include "svc_capture.h"

#include <inttypes.h>
#include <stdbool.h>
#include <string.h>

#include "app_events.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "svc_note.h"
#include "svc_outbox.h"
#include "sys_time.h"

#define US_PER_MS 1000

static const char *TAG = "svc_capture";

static svc_capture_config_t s_cfg;
static record_t *s_record;

esp_err_t svc_capture_init(const svc_capture_config_t *cfg)
{
    if (cfg == NULL) { return ESP_ERR_INVALID_ARG; }
    s_cfg = *cfg;
    s_record = heap_caps_malloc(sizeof(*s_record), MALLOC_CAP_SPIRAM);
    return s_record != NULL ? ESP_OK : ESP_ERR_NO_MEM;
}

static void beep(app_tone_t tone)
{
    if (s_cfg.speak == NULL) { return; }
    const app_speak_req_t request = {.tone = tone};
    if (xQueueSend(s_cfg.speak, &request, 0) != pdTRUE) {
        ESP_LOGW(TAG, "q_speak full, tone %d lost", (int)tone);
    }
}

static capture_mode_t mode_of(app_vision_mode_t mode)
{
    if (mode == APP_VISION_DETECT) { return CAPTURE_MODE_DETECT; }
    return mode == APP_VISION_JOURNAL ? CAPTURE_MODE_JOURNAL : CAPTURE_MODE_PHOTO;
}

static svc_note_t journal(uint32_t seq, svc_vision_shot_t *shot)
{
    // The note and the picture at once: the picture shows the scene of the command (KEHOACH 3.19).
    const esp_err_t asked = svc_note_start(seq);
    if (asked != ESP_OK) { ESP_LOGW(TAG, "no note: %s", esp_err_to_name(asked)); }
    *shot = svc_vision_shoot(seq, true, false);
    svc_note_t note = {.seq = seq};
    const esp_err_t heard = asked == ESP_OK ? svc_note_wait(seq, &note) : asked;
    if (heard != ESP_OK && asked == ESP_OK) { ESP_LOGW(TAG, "note: %s", esp_err_to_name(heard)); }
    const esp_err_t sent = note.saved ? svc_note_send() : ESP_ERR_INVALID_STATE;
    // No session leaves the note on the card for svc_outbox: nothing to warn about.
    if (sent != ESP_OK && sent != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "note not sent: %s", esp_err_to_name(sent));
    }
    return note;
}

static void fill_record(const svc_vision_capture_t *capture, uint32_t started_ms,
                        const svc_vision_shot_t *shot, const svc_note_t *note)
{
    memset(s_record, 0, sizeof(*s_record));
    s_record->capture = (capture_t){
        .boot = s_cfg.boot,
        .capture_seq = capture->seq,
        .mode = mode_of(capture->mode),
        .uptime_ms = started_ms,
        .image_bytes = shot->bytes,
        .sent = shot->sent,
        .saved = shot->saved,
        .note_ms = note->ms,
        .note_bytes = note->bytes,
        .note_dropped = note->dropped,
        .note_saved = note->saved,
    };
    s_record->capture.has_utc_ms =
        sys_time_utc_ms((int64_t)started_ms * US_PER_MS, &s_record->capture.utc_ms);
    if (shot->found != NULL) {
        s_record->detections = *shot->found;
        s_record->has_detections = true;
    }
    s_record->has_sensors = s_cfg.sensors != NULL && s_cfg.sensors(&s_record->sensors);
}

void svc_capture_keep(const svc_vision_capture_t *capture)
{
    const uint32_t started_ms = (uint32_t)(esp_timer_get_time() / US_PER_MS);
    svc_vision_shot_t shot = {0};
    svc_note_t note = {.seq = capture->seq};
    if (capture->mode == APP_VISION_JOURNAL) {
        note = journal(capture->seq, &shot);
        beep(note.saved && (shot.saved || shot.sent) ? APP_TONE_DONE : APP_TONE_FAIL);
    } else {
        shot = svc_vision_shoot(capture->seq, capture->mode == APP_VISION_DETECT, true);
    }
    // Nothing came of it: no picture, boxes or note to keep or send later.
    if ((shot.bytes == 0 && note.bytes == 0 && shot.found == NULL) || s_record == NULL) { return; }
    fill_record(capture, started_ms, &shot, &note);
    const svc_outbox_kept_t kept = svc_outbox_keep(s_record);
    ESP_LOGI(TAG, "B%" PRIu32 "/%06" PRIu32 ": picture %s%s, note %" PRIu32 " B, record %s%s", s_cfg.boot,
             capture->seq, shot.sent ? "sent" : "not sent", shot.saved ? ", kept" : "", note.bytes,
             kept.sent ? "sent" : "not sent", kept.saved ? ", kept" : "");
}
