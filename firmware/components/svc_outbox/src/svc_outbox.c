#include "svc_outbox.h"

#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include "app_err.h"
#include "drv_sd.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "gen_topics.h"
#include "gen_vision.h"
#include "net_mqtt.h"
#include "sys_time.h"

#define PATH_BYTES 40
#define MARKS_DIR "IMG/PND"
#define MARK_EXTENSION ".PND"
#define IDLE_WAIT_MS 1000
#define DECIMAL 10
#define US_PER_MS 1000
#define US_PER_S 1000000

typedef enum {
    PART_PICTURE = 0,
    PART_NOTE,
    PART_RECORD,
} part_t;

typedef struct {
    uint32_t boot;
    uint32_t seq;
    bool acked;
} pending_t;

typedef struct {
    const char *stem;
    bool plain; // written as <seq>.JSN, not a copy's name
} card_name_t;

typedef struct {
    record_t record; // the pending capture being sent
    char text[NET_MQTT_RECORD_TEXT_BYTES];
    pending_t pending[GEN_VISION_OUTBOX_BATCH];
    size_t pending_count;
    size_t next; // pending's capture being sent; pending_count when none
    bool opened; // record holds pending[next]
    part_t part;
    size_t chunk;
    bool online;
    int64_t scan_at_us;
} outbox_t;

static const char *TAG = "svc_outbox";

static svc_outbox_config_t s_cfg;
// PSRAM: internal RAM moves detect's loan in 2 KB steps (measurements/board_c.md §4).
static outbox_t *s;

esp_err_t svc_outbox_init(const svc_outbox_config_t *cfg)
{
    if (cfg == NULL) { return ESP_ERR_INVALID_ARG; }
    if (s != NULL) { return ESP_ERR_INVALID_STATE; }
    s_cfg = *cfg;
    s = heap_caps_calloc(1, sizeof(*s), MALLOC_CAP_SPIRAM);
    return s != NULL ? ESP_OK : ESP_ERR_NO_MEM;
}

static uint32_t uptime_ms(void)
{
    return (uint32_t)(esp_timer_get_time() / US_PER_MS);
}

static void stem_of(uint32_t boot, uint32_t seq, char *out, size_t bytes)
{
    snprintf(out, bytes, "IMG/B%" PRIu32 "/%06" PRIu32, boot, seq);
}

static void file_of(uint32_t boot, uint32_t seq, const char *extension, char *out, size_t bytes)
{
    snprintf(out, bytes, "IMG/B%" PRIu32 "/%06" PRIu32 ".%s", boot, seq, extension);
}

static void mark_of(uint32_t boot, uint32_t seq, char *out, size_t bytes)
{
    snprintf(out, bytes, MARKS_DIR "/B%" PRIu32 "_%06" PRIu32 MARK_EXTENSION, boot, seq);
}

static esp_err_t write_card(void *ctx, const char *text, size_t bytes)
{
    card_name_t *name = ctx;
    char path[PATH_BYTES], plain[PATH_BYTES];
    esp_err_t err = drv_sd_name(name->stem, "JSN", path, sizeof(path));
    if (err == ESP_OK) { err = drv_sd_write(path, (const uint8_t *)text, bytes); }
    snprintf(plain, sizeof(plain), "%s.JSN", name->stem);
    name->plain = err == ESP_OK && strcmp(path, plain) == 0;
    return err;
}

static bool keep_on_card(record_t *rec)
{
    if (!drv_sd_ready()) { return false; }
    char stem[PATH_BYTES], mark[PATH_BYTES];
    stem_of(rec->capture.boot, rec->capture.capture_seq, stem, sizeof(stem));
    card_name_t name = {.stem = stem};
    esp_err_t err = net_mqtt_print_record(rec, write_card, &name);
    mark_of(rec->capture.boot, rec->capture.capture_seq, mark, sizeof(mark));
    // A copy's name means an older run left this stem: the late path reads <seq>.* alone, so no mark.
    if (err == ESP_OK && name.plain) { err = drv_sd_write(mark, (const uint8_t *)"", 0); }
    if (err != ESP_OK) { ESP_LOGW(TAG, "%s record not kept: %s", stem, esp_err_to_name(err)); }
    return err == ESP_OK;
}

svc_outbox_kept_t svc_outbox_keep(record_t *rec)
{
    rec->capture.late = false;
    rec->capture.sent_boot = s_cfg.boot;
    rec->capture.sent_uptime_ms = uptime_ms();
    const esp_err_t told = net_mqtt_publish_record(rec);
    if (told != ESP_OK && told != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "record not sent: %s", esp_err_to_name(told));
    }
    return (svc_outbox_kept_t){.sent = told == ESP_OK, .saved = keep_on_card(rec)};
}

static void take_acks(void)
{
    ack_t ack;
    while (s_cfg.acks != NULL && xQueueReceive(s_cfg.acks, &ack, 0) == pdTRUE) {
        char mark[PATH_BYTES];
        mark_of(ack.boot, ack.capture_seq, mark, sizeof(mark));
        const esp_err_t removed = drv_sd_remove(mark);
        for (size_t i = 0; s != NULL && i < s->pending_count; ++i) {
            if (s->pending[i].boot == ack.boot && s->pending[i].seq == ack.capture_seq) {
                s->pending[i].acked = true;
            }
        }
        ESP_LOGI(TAG, "B%" PRIu32 "/%06" PRIu32 " acked: %s", ack.boot, ack.capture_seq,
                 esp_err_to_name(removed));
    }
}

static bool older(uint32_t boot, uint32_t seq, const pending_t *than)
{
    return boot < than->boot || (boot == than->boot && seq < than->seq);
}

static void add_pending(uint32_t boot, uint32_t seq)
{
    size_t at = s->pending_count;
    if (at == GEN_VISION_OUTBOX_BATCH) {
        if (!older(boot, seq, &s->pending[at - 1])) { return; }
        at--;
    } else {
        s->pending_count++;
    }
    for (; at > 0 && older(boot, seq, &s->pending[at - 1]); --at) {
        s->pending[at] = s->pending[at - 1];
    }
    s->pending[at] = (pending_t){.boot = boot, .seq = seq};
}

static void each_mark(void *ctx, const char *name, size_t bytes, bool is_dir)
{
    (void)ctx;
    (void)bytes;
    char *end = NULL;
    if (is_dir || name[0] != 'B') { return; }
    const unsigned long boot = strtoul(name + 1, &end, DECIMAL);
    if (end == name + 1 || *end != '_') { return; }
    const char *digits = end + 1;
    const unsigned long seq = strtoul(digits, &end, DECIMAL);
    if (end != digits && strcasecmp(end, MARK_EXTENSION) == 0) { add_pending((uint32_t)boot, (uint32_t)seq); }
}

static void scan(void)
{
    s->pending_count = 0;
    s->next = 0;
    s->opened = false;
    const esp_err_t err = drv_sd_list(MARKS_DIR, each_mark, NULL);
    if (err != ESP_OK && err != ESP_ERR_NOT_FOUND) {
        ESP_LOGW(TAG, "marks not read: %s", esp_err_to_name(err));
    }
    if (s->pending_count > 0) { ESP_LOGI(TAG, "%u captures to send again", (unsigned)s->pending_count); }
}

static void finish(void)
{
    s->next++;
    s->opened = false;
}

// What the card holds of a part named in the record, so the server never waits for a file that is gone.
static uint32_t on_card(const pending_t *at, const char *extension, uint32_t bytes)
{
    char path[PATH_BYTES];
    size_t found = 0;
    file_of(at->boot, at->seq, extension, path, sizeof(path));
    return bytes > 0 && drv_sd_size(path, &found) == ESP_OK && found == bytes ? bytes : 0;
}

static esp_err_t open_capture(const pending_t *at)
{
    char path[PATH_BYTES];
    size_t got = 0;
    file_of(at->boot, at->seq, "JSN", path, sizeof(path));
    esp_err_t err = drv_sd_read(path, 0, (uint8_t *)s->text, NET_MQTT_RECORD_TEXT_BYTES, &got);
    if (err == ESP_OK) { err = got < NET_MQTT_RECORD_TEXT_BYTES ? ESP_OK : ESP_ERR_INVALID_SIZE; }
    if (err == ESP_OK) { err = net_mqtt_parse_record(s->text, got, &s->record); }
    if (err != ESP_OK) { return err; }
    capture_t *capture = &s->record.capture;
    capture->image_bytes = on_card(at, "JPG", capture->image_bytes);
    capture->saved = capture->image_bytes > 0;
    capture->note_bytes = on_card(at, "WAV", capture->note_bytes);
    capture->note_saved = capture->note_bytes > 0;
    s->part = PART_PICTURE;
    s->chunk = 0;
    s->opened = true;
    return ESP_OK;
}

static esp_err_t fill_from_card(const void *ctx, size_t offset, uint8_t *data, size_t bytes)
{
    size_t got = 0;
    const esp_err_t err = drv_sd_read(ctx, offset, data, bytes, &got);
    if (err != ESP_OK) { return err; }
    return got == bytes ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

static esp_err_t send_chunk(const pending_t *at, gen_topic_id_t topic, uint16_t flags, const char *extension,
                            uint32_t bytes, bool *done)
{
    char path[PATH_BYTES];
    file_of(at->boot, at->seq, extension, path, sizeof(path));
    const net_mqtt_chunks_t chunks = {
        .topic = topic,
        .flags = flags | GEN_VISION_CHUNK_FLAG_LATE,
        .boot = at->boot,
        .seq = at->seq,
        .total_bytes = bytes,
        .fill = fill_from_card,
        .ctx = path,
        .first_index = s->chunk,
        .chunks_max = 1,
    };
    *done = bytes == 0;
    if (*done) { return ESP_OK; }
    const esp_err_t err = net_mqtt_publish_chunks(&chunks);
    if (err == ESP_OK) { *done = ++s->chunk * GEN_VISION_CHUNK_PAYLOAD_BYTES >= bytes; }
    return err;
}

static esp_err_t send_record(void)
{
    capture_t *capture = &s->record.capture;
    capture->late = true;
    capture->sent_boot = s_cfg.boot;
    capture->sent_uptime_ms = uptime_ms();
    // The clock may have come after the capture, but it only knows this run's uptime (KEHOACH 3.19).
    if (!capture->has_utc_ms && capture->boot == s_cfg.boot) {
        capture->has_utc_ms = sys_time_utc_ms((int64_t)capture->uptime_ms * US_PER_MS, &capture->utc_ms);
    }
    return net_mqtt_publish_record(&s->record);
}

static esp_err_t send_part(const pending_t *at, bool *done)
{
    const capture_t *capture = &s->record.capture;
    switch (s->part) {
    case PART_PICTURE:
        return send_chunk(at, GEN_TOPIC_IMAGE, GEN_VISION_CHUNK_FLAG_FULL, "JPG", capture->image_bytes, done);
    case PART_NOTE: return send_chunk(at, GEN_TOPIC_NOTE, 0, "WAV", capture->note_bytes, done);
    default: *done = true; return send_record();
    }
}

static void send_next(void)
{
    const pending_t *at = &s->pending[s->next];
    if (at->acked) {
        finish();
        return;
    }
    esp_err_t err = s->opened ? ESP_OK : open_capture(at);
    if (err == APP_ERR_RECORD_INVALID || err == ESP_FAIL || err == ESP_ERR_INVALID_SIZE) {
        char mark[PATH_BYTES];
        mark_of(at->boot, at->seq, mark, sizeof(mark));
        ESP_LOGW(TAG, "B%" PRIu32 "/%06" PRIu32 " dropped: .JSN %s", at->boot, at->seq, esp_err_to_name(err));
        (void)drv_sd_remove(mark);
    }
    if (err != ESP_OK) {
        finish();
        return;
    }
    bool done = false;
    err = send_part(at, &done);
    // Full outbox, arena or session: the same chunk again on a later step.
    if (err == ESP_ERR_NOT_FINISHED || err == ESP_ERR_NO_MEM || err == ESP_ERR_INVALID_STATE) { return; }
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "B%" PRIu32 "/%06" PRIu32 " part %d: %s", at->boot, at->seq, (int)s->part,
                 esp_err_to_name(err));
        finish();
        return;
    }
    if (!done) { return; }
    s->chunk = 0;
    if (s->part != PART_RECORD) {
        s->part++;
        return;
    }
    ESP_LOGI(TAG, "B%" PRIu32 "/%06" PRIu32 " sent again: picture %" PRIu32 " B, note %" PRIu32 " B",
             at->boot, at->seq, s->record.capture.image_bytes, s->record.capture.note_bytes);
    finish();
}

uint32_t svc_outbox_step(void)
{
    take_acks();
    if (s == NULL) { return IDLE_WAIT_MS; }
    const bool online = net_mqtt_connected();
    if (online && !s->online) {
        // A new session: what the old one held may be lost, so the pass starts over.
        s->next = s->pending_count;
        s->scan_at_us = 0;
    }
    s->online = online;
    if (!online || !drv_sd_ready()) { return IDLE_WAIT_MS; }
    const int64_t now_us = esp_timer_get_time();
    if (s->next >= s->pending_count) {
        if (now_us < s->scan_at_us) { return IDLE_WAIT_MS; }
        s->scan_at_us = now_us + (int64_t)GEN_VISION_OUTBOX_RETRY_S * US_PER_S;
        scan();
        if (s->pending_count == 0) { return IDLE_WAIT_MS; }
    }
    send_next();
    return GEN_VISION_OUTBOX_STEP_WAIT_MS;
}
