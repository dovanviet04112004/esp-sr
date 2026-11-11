#include "svc_vision.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "ai_engine.h"
#include "app_err.h"
#include "app_events.h"
#include "bsp_board.h"
#include "drv_camera.h"
#include "drv_sd.h"
#include "esp_heap_caps.h"
#include "esp_jpeg_dec.h"
#include "esp_log.h"
#include "esp_rom_crc.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gen_payload.h"
#include "gen_vision.h"
#include "net_mqtt.h"

#define REQUEST_WAIT_MS 1000
#define PATH_BYTES 32
#define NAME_COPIES_MAX 16 // pictures of one name a lost NVS can leave
#define RGB_BYTES_PER_PIXEL 3
#define DECODED_WIDTH GEN_VISION_DECODE_WIDTH
#define DECODED_HEIGHT GEN_VISION_DECODE_HEIGHT
#define UPRIGHT_WIDTH DECODED_HEIGHT
#define UPRIGHT_HEIGHT DECODED_WIDTH
#define RGB_BYTES (DECODED_WIDTH * DECODED_HEIGHT * RGB_BYTES_PER_PIXEL)
#define RGB_ALIGN_BYTES 16 // jpeg_dec_process's output
#define SCALE_STEP_PIXELS 8
#define CHUNK_BYTES (GEN_VISION_CHUNK_HEADER_BYTES + GEN_VISION_CHUNK_PAYLOAD_BYTES)
#define US_PER_MS 1000

_Static_assert(DECODED_WIDTH % SCALE_STEP_PIXELS == 0 && DECODED_HEIGHT % SCALE_STEP_PIXELS == 0,
               "esp_new_jpeg scales to multiples of 8");
_Static_assert(GEN_VISION_TURN_DEG == 90, "decode_picture and kExifTurn say a quarter turn clockwise");

// APP1 with one EXIF tag, Orientation 6: viewers stand the file up, no recompression (KEHOACH 3.19).
static const uint8_t kExifTurn[] = {0xFF, 0xE1, 0x00, 0x22, 'E', 'x', 'i', 'f', 0,    0,    'I', 'I',
                                    0x2A, 0,    8,    0,    0,   0,   1,   0,   0x12, 0x01, 3,   0,
                                    1,    0,    0,    0,    6,   0,   0,   0,   0,    0,    0,   0};

static const char *TAG = "svc_vision";

static svc_vision_config_t s_cfg;
static uint8_t *s_rgb, *s_chunk;
static ai_engine_box_t *s_boxes;
static detections_t *s_found;
static bool s_ready;
static uint32_t s_seq;
static uint32_t s_events_lost;
static uint32_t s_preview_seq;
static bool s_previewing;
static int64_t s_preview_until_us;

esp_err_t svc_vision_init(const svc_vision_config_t *cfg)
{
    if (cfg == NULL || cfg->requests == NULL || cfg->events == NULL) { return ESP_ERR_INVALID_ARG; }
    if (s_cfg.requests != NULL) { return ESP_ERR_INVALID_STATE; }
    // First: nhin_task runs whatever comes of the buffers, and answers requests without them (KEHOACH 3.19).
    s_cfg = *cfg;
    s_rgb = heap_caps_aligned_alloc(RGB_ALIGN_BYTES, RGB_BYTES, MALLOC_CAP_SPIRAM);
    s_chunk = heap_caps_malloc(CHUNK_BYTES, MALLOC_CAP_SPIRAM);
    s_boxes = heap_caps_malloc(GEN_VISION_BOXES_MAX * sizeof(*s_boxes), MALLOC_CAP_SPIRAM);
    s_found = heap_caps_malloc(sizeof(*s_found), MALLOC_CAP_SPIRAM);
    s_ready = s_rgb != NULL && s_chunk != NULL && s_boxes != NULL && s_found != NULL;
    return s_ready ? ESP_OK : ESP_ERR_NO_MEM;
}

static void raise_event(app_event_t *event)
{
    if (xQueueSend(s_cfg.events, event, 0) != pdTRUE) {
        s_events_lost++;
        ESP_LOGW(TAG, "q_event_up full, event lost");
    }
}

static void raise_error(const char *code)
{
    app_event_t event = {.kind = APP_EVT_ERROR, .doa_deg = -1};
    strlcpy(event.code, code, sizeof(event.code));
    raise_event(&event);
}

static bool keep_on_card(const drv_camera_frame_t *frame, uint32_t seq)
{
    char path[PATH_BYTES];
    snprintf(path, sizeof(path), "IMG/B%" PRIu32 "/%06" PRIu32 ".JPG", s_cfg.boot, seq);
    // A lost NVS counts boots again: a taken name gets a suffix, the older picture stays (KEHOACH 3.19).
    for (uint32_t copy = 1; copy <= NAME_COPIES_MAX && drv_sd_exists(path); copy++) {
        snprintf(path, sizeof(path), "IMG/B%" PRIu32 "/%06" PRIu32 "_%" PRIu32 ".JPG", s_cfg.boot, seq, copy);
    }
    const esp_err_t err = drv_sd_write(path, frame->jpeg, frame->bytes);
    if (err != ESP_OK) { ESP_LOGW(TAG, "%s not kept: %s", path, esp_err_to_name(err)); }
    return err == ESP_OK;
}

static esp_err_t send_chunks(const drv_camera_frame_t *frame, uint32_t seq, uint16_t flags,
                             esp_err_t (*publish)(const uint8_t *chunk, size_t bytes))
{
    const uint16_t count =
        (uint16_t)((frame->bytes + GEN_VISION_CHUNK_PAYLOAD_BYTES - 1) / GEN_VISION_CHUNK_PAYLOAD_BYTES);
    for (uint16_t index = 0; index < count; ++index) {
        const size_t offset = (size_t)index * GEN_VISION_CHUNK_PAYLOAD_BYTES;
        const size_t left = frame->bytes - offset;
        const size_t bytes = left < GEN_VISION_CHUNK_PAYLOAD_BYTES ? left : GEN_VISION_CHUNK_PAYLOAD_BYTES;
        const gen_vision_chunk_header_t header = {
            .magic = GEN_VISION_CHUNK_MAGIC,
            .version = GEN_VISION_CHUNK_VERSION,
            .index = index,
            .count = count,
            .flags = flags,
            .boot = s_cfg.boot,
            .seq = seq,
            .total_bytes = (uint32_t)frame->bytes,
            .crc32 = esp_rom_crc32_le(0, frame->jpeg + offset, bytes),
        };
        memcpy(s_chunk, &header, sizeof(header));
        memcpy(s_chunk + sizeof(header), frame->jpeg + offset, bytes);
        const esp_err_t err = publish(s_chunk, sizeof(header) + bytes);
        if (err != ESP_OK) { return err; }
    }
    return ESP_OK;
}

static esp_err_t decode_picture(const drv_camera_frame_t *frame)
{
    jpeg_dec_config_t cfg = DEFAULT_JPEG_DEC_CONFIG();
    cfg.output_type = JPEG_PIXEL_FORMAT_RGB888;
    cfg.scale.width = DECODED_WIDTH;
    cfg.scale.height = DECODED_HEIGHT;
    cfg.rotate = JPEG_ROTATE_90D;
    jpeg_dec_handle_t decoder = NULL;
    if (jpeg_dec_open(&cfg, &decoder) != JPEG_ERR_OK) { return ESP_ERR_NO_MEM; }
    jpeg_dec_io_t io = {.inbuf = (uint8_t *)frame->jpeg, .inbuf_len = (int)frame->bytes, .outbuf = s_rgb};
    jpeg_dec_header_info_t header;
    jpeg_error_t err = jpeg_dec_parse_header(decoder, &io, &header);
    if (err == JPEG_ERR_OK) { err = jpeg_dec_process(decoder, &io); }
    jpeg_dec_close(decoder);
    return err == JPEG_ERR_OK ? ESP_OK : ESP_FAIL;
}

static uint16_t pixel(int16_t value)
{
    return value < 0 ? 0 : (uint16_t)value;
}

static esp_err_t report_boxes(uint32_t seq, size_t found, uint32_t detect_ms)
{
    memset(s_found, 0, sizeof(*s_found));
    s_found->boot = s_cfg.boot;
    s_found->capture_seq = seq;
    s_found->width = UPRIGHT_WIDTH;
    s_found->height = UPRIGHT_HEIGHT;
    s_found->detect_ms = detect_ms;
    s_found->boxes_count = (uint8_t)found;
    for (size_t i = 0; i < found; ++i) {
        const ai_engine_box_t *box = &s_boxes[i];
        const char *name = ai_engine_detect_name(box->cls);
        strlcpy(s_found->boxes[i].name, name != NULL ? name : "unknown", sizeof(s_found->boxes[i].name));
        s_found->boxes[i].score_permille = box->score_permille;
        s_found->boxes[i].x1 = pixel(box->x1);
        s_found->boxes[i].y1 = pixel(box->y1);
        s_found->boxes[i].x2 = pixel(box->x2);
        s_found->boxes[i].y2 = pixel(box->y2);
    }
    return net_mqtt_publish_detections(s_found);
}

static esp_err_t detect_and_send(uint32_t seq)
{
    size_t found = 0;
    const UBaseType_t own_priority = uxTaskPriorityGet(NULL);
    // Above nhan_task for this run alone: q_clean holds the hops, and the user is waiting (KEHOACH 3.18).
    vTaskPrioritySet(NULL, s_cfg.detect_priority);
    const int64_t started_us = esp_timer_get_time();
    esp_err_t err =
        ai_engine_detect_run(s_rgb, UPRIGHT_WIDTH, UPRIGHT_HEIGHT, s_boxes, GEN_VISION_BOXES_MAX, &found);
    const uint32_t detect_ms = (uint32_t)((esp_timer_get_time() - started_us) / US_PER_MS);
    if (err == ESP_OK) {
        // Not detect's failure: a QoS 1 copy stays in the outbox (KEHOACH 3.19).
        const esp_err_t reported = report_boxes(seq, found, detect_ms);
        if (reported != ESP_OK) { ESP_LOGW(TAG, "boxes not sent: %s", esp_err_to_name(reported)); }
    }
    vTaskPrioritySet(NULL, own_priority);
    return err;
}

static void beep(app_tone_t tone)
{
    if (s_cfg.speak == NULL) { return; }
    const app_speak_req_t request = {.tone = tone};
    if (xQueueSend(s_cfg.speak, &request, 0) != pdTRUE) {
        ESP_LOGW(TAG, "q_speak full, tone %d lost", (int)tone);
    }
}

static bool detect_picture(const drv_camera_frame_t *frame, uint32_t seq)
{
    esp_err_t err = decode_picture(frame);
    if (err == ESP_OK) { err = detect_and_send(seq); }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "detect: %s", esp_err_to_name(err));
        raise_error(APP_CODE_DETECT_FAIL);
    }
    return err == ESP_OK;
}

static esp_err_t take_picture(drv_camera_frame_t *frame)
{
    bsp_board_camera_light(true);
    esp_err_t err = drv_camera_wake();
    if (err == ESP_OK) {
        // A soft picture beats none: a failed focus still shoots.
        const esp_err_t focused = drv_camera_focus();
        if (focused != ESP_OK && focused != ESP_ERR_NOT_SUPPORTED) {
            ESP_LOGW(TAG, "focus: %s", esp_err_to_name(focused));
        }
        err = drv_camera_grab(frame);
    }
    bsp_board_camera_light(false);
    return err;
}

static esp_err_t shoot(drv_camera_frame_t *frame, drv_camera_size_t size)
{
    esp_err_t err = drv_camera_use(size);
    if (err == ESP_OK) { err = take_picture(frame); }
    // The sensor stops first: from here on the listener gets PSRAM's bandwidth back.
    const esp_err_t rested = drv_camera_rest();
    if (rested != ESP_OK) { ESP_LOGW(TAG, "rest: %s", esp_err_to_name(rested)); }
    // Previews and the next command start from detect's frame (KEHOACH 3.19).
    const esp_err_t back = drv_camera_use(DRV_CAMERA_SIZE_DETECT);
    if (back != ESP_OK) { ESP_LOGW(TAG, "detect's frame: %s", esp_err_to_name(back)); }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "no picture: %s", esp_err_to_name(err));
        raise_error(APP_CODE_CAMERA_FAIL);
        return err;
    }
    const esp_err_t tagged = drv_camera_insert(frame, kExifTurn, sizeof(kExifTurn));
    if (tagged != ESP_OK) { ESP_LOGW(TAG, "orientation not tagged: %s", esp_err_to_name(tagged)); }
    return ESP_OK;
}

static bool answer_detect(uint32_t seq, bool *toned)
{
    const int64_t started_us = esp_timer_get_time();
    drv_camera_frame_t frame = {0};
    *toned = true;
    if (shoot(&frame, DRV_CAMERA_SIZE_DETECT) != ESP_OK) {
        beep(APP_TONE_FAIL);
        return false;
    }
    const int64_t shot_us = esp_timer_get_time();
    const esp_err_t sent = send_chunks(&frame, seq, 0, net_mqtt_publish_image);
    if (sent != ESP_OK) { ESP_LOGW(TAG, "picture not sent: %s", esp_err_to_name(sent)); }
    const int64_t sent_us = esp_timer_get_time();
    const bool answered = detect_picture(&frame, seq);
    // Unsent but answered, the tone waits to learn whether the full picture reaches a place (KEHOACH 3.19).
    *toned = sent == ESP_OK || !answered;
    if (*toned) { beep(sent == ESP_OK && answered ? APP_TONE_DONE : APP_TONE_FAIL); }
    const unsigned bytes = (unsigned)frame.bytes;
    drv_camera_release(&frame);
    ESP_LOGI(TAG, "B%" PRIu32 "/%06" PRIu32 ": %u bytes, shot %lld ms, sent %lld ms, detect %lld ms",
             s_cfg.boot, seq, bytes, (shot_us - started_us) / US_PER_MS, (sent_us - shot_us) / US_PER_MS,
             (esp_timer_get_time() - sent_us) / US_PER_MS);
    return true;
}

static void send_full(uint32_t seq, bool sounds)
{
    const int64_t started_us = esp_timer_get_time();
    drv_camera_frame_t frame = {0};
    if (shoot(&frame, DRV_CAMERA_SIZE_FULL) != ESP_OK) {
        if (sounds) { beep(APP_TONE_FAIL); }
        return;
    }
    const int64_t shot_us = esp_timer_get_time();
    const esp_err_t sent = send_chunks(&frame, seq, GEN_VISION_CHUNK_FLAG_FULL, net_mqtt_publish_image);
    if (sent != ESP_OK) { ESP_LOGW(TAG, "full picture not sent: %s", esp_err_to_name(sent)); }
    if (sounds && sent == ESP_OK) { beep(APP_TONE_DONE); }
    const int64_t sent_us = esp_timer_get_time();
    const bool saved = keep_on_card(&frame, seq);
    if (sounds && sent != ESP_OK) { beep(saved ? APP_TONE_DONE : APP_TONE_FAIL); }
    app_event_t event = {
        .kind = APP_EVT_CAPTURE,
        .doa_deg = -1,
        .boot = s_cfg.boot,
        .capture_seq = seq,
        .image_bytes = (uint32_t)frame.bytes,
        .saved = saved,
    };
    raise_event(&event);
    drv_camera_release(&frame);
    ESP_LOGI(TAG, "B%" PRIu32 "/%06" PRIu32 " full: %u bytes, shot %lld ms, sent %lld ms, card %lld ms",
             s_cfg.boot, seq, (unsigned)event.image_bytes, (shot_us - started_us) / US_PER_MS,
             (sent_us - shot_us) / US_PER_MS, (esp_timer_get_time() - sent_us) / US_PER_MS);
}

static void carry_out(const app_vision_req_t *request)
{
    const uint32_t seq = ++s_seq;
    bool toned = false;
    if (request->mode == APP_VISION_DETECT && !answer_detect(seq, &toned)) { return; }
    send_full(seq, !toned);
}

static void end_preview(void)
{
    s_previewing = false;
    const esp_err_t rested = drv_camera_rest();
    if (rested != ESP_OK) { ESP_LOGW(TAG, "rest: %s", esp_err_to_name(rested)); }
    const esp_err_t back = drv_camera_use(DRV_CAMERA_SIZE_DETECT);
    if (back != ESP_OK) { ESP_LOGW(TAG, "detect's frame: %s", esp_err_to_name(back)); }
    bsp_board_camera_light(false);
}

static void hold_preview(void)
{
    if (!s_previewing) {
        bsp_board_camera_light(true);
        // Focus is set on the picture sent home (KEHOACH 3.19).
        esp_err_t woken = drv_camera_use(DRV_CAMERA_SIZE_FULL);
        if (woken == ESP_OK) { woken = drv_camera_wake(); }
        if (woken != ESP_OK) {
            // No frames will come: a preview would spin this task with the light on.
            ESP_LOGW(TAG, "preview: %s", esp_err_to_name(woken));
            end_preview();
            return;
        }
        s_previewing = true;
    }
    s_preview_until_us = esp_timer_get_time() + (int64_t)GEN_VISION_PREVIEW_HOLD_MS * US_PER_MS;
}

static void send_preview_frame(void)
{
    drv_camera_frame_t frame = {0};
    if (drv_camera_grab(&frame) != ESP_OK) { return; }
    const esp_err_t sent =
        send_chunks(&frame, ++s_preview_seq, GEN_VISION_CHUNK_FLAG_PREVIEW, net_mqtt_publish_preview);
    if (sent != ESP_OK) { ESP_LOGD(TAG, "preview frame lost: %s", esp_err_to_name(sent)); }
    drv_camera_release(&frame);
}

static void refuse(const app_vision_req_t *request)
{
    if (request->mode == APP_VISION_PREVIEW) { return; }
    raise_error(APP_CODE_CAMERA_FAIL);
    beep(APP_TONE_FAIL);
}

void svc_vision_step(void)
{
    app_vision_req_t request;
    const TickType_t wait = s_previewing ? 0 : pdMS_TO_TICKS(REQUEST_WAIT_MS);
    // Peeked, not taken: the full queue is what refuses a second command during this picture (KEHOACH 5.3).
    if (xQueuePeek(s_cfg.requests, &request, wait) == pdTRUE) {
        if (!s_ready) {
            refuse(&request);
        } else if (request.mode == APP_VISION_PREVIEW) {
            hold_preview();
        } else {
            if (s_previewing) { end_preview(); }
            carry_out(&request);
        }
        xQueueReset(s_cfg.requests);
    }
    if (s_previewing && esp_timer_get_time() > s_preview_until_us) { end_preview(); }
    if (s_previewing) { send_preview_frame(); }
}

uint32_t svc_vision_events_lost(void)
{
    return s_events_lost;
}
