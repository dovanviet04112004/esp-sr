#include "drv_camera.h"

#include <inttypes.h>
#include <math.h>
#include <stdbool.h>
#include <string.h>

#include "app_config.h"
#include "esp_camera.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gen_vision.h"

#define FB_COUNT 1
#define AF_LOAD_TIMEOUT_MS 5000
#define AF_POLL_MS 50
#define US_PER_MS 1000
#define REG_BYTE_MASK 0xFF
#define REG_WORD_MASK 0xFFFF
#define REG_THREE_BYTES_MASK 0xFFFFFF
// OV5640 software power down, datasheet rev 2.51 table 4-2.
#define SYSTEM_CTRL0_REG 0x3008
#define SYSTEM_CTRL0_STANDBY 0x40
#define WAKE_SETTLE_MS 20
#define EXPOSURE_REG 0x3500 // 20 bits of 1/16 line, what AEC runs at
#define EXPOSURE_MASK 0xFFFFF
#define EXPOSURE_PER_LINE 16
#define GAIN_REG 0x350A // 10 bits, what AGC runs at
#define GAIN_MASK 0x3FF
#define AEC_MANUAL_REG 0x3503
#define AEC_AGC_MANUAL 0x03 // bit 0 AEC, bit 1 AGC by hand
#define LINE_TOTAL_REG 0x380C
#define BAND_50HZ_STEP_REG 0x3A08
#define BAND_HALF_PERIOD_US 10000 // 50 Hz mains peaks twice per cycle
#define PLL_MULTIPLIER_REG 0x3036
// set_framesize's PLL for a JPEG frame divides its multiplier by 2, 4, 2.5 and 4 (esp32-camera ov5640.c).
#define PLL_DIVIDERS 80
#define JPEG_SOI_BYTES 2

static const char *TAG = "drv_camera";

static sensor_t *s_sensor;
static bool s_can_focus;
static drv_camera_size_t s_size;
static bool s_size_known;
static const uint8_t kQualityLadder[] = GEN_VISION_FULL_QUALITY_LADDER;
static int s_full_quality = GEN_VISION_FULL_JPEG_QUALITY;

// The best rung whose predicted size fits, from one frame's bytes at a known quality (KEHOACH 3.19).
static int fitting_quality(size_t bytes, int at_quality)
{
    const float budget = GEN_VISION_FULL_FILL_MAX * (float)CONFIG_CAMERA_JPEG_MODE_FRAME_SIZE;
    for (size_t i = 0; i < GEN_VISION_FULL_QUALITY_STEPS; i++) {
        const float ratio = (float)at_quality / (float)kQualityLadder[i];
        if ((float)bytes * powf(ratio, GEN_VISION_FULL_SIZE_EXPONENT) <= budget) { return kQualityLadder[i]; }
    }
    return kQualityLadder[GEN_VISION_FULL_QUALITY_STEPS - 1];
}

static int coarser_than(int quality)
{
    for (size_t i = 0; i < GEN_VISION_FULL_QUALITY_STEPS; i++) {
        if (kQualityLadder[i] > quality) { return kQualityLadder[i]; }
    }
    return quality;
}

static bool full_size(void)
{
    return s_size_known && s_size == DRV_CAMERA_SIZE_FULL;
}

typedef struct {
    int line_total; // points a line takes
    int multiplier; // of the PLL, which sysclk follows
} line_t;

static bool read_line(line_t *line)
{
    line->line_total = s_sensor->get_reg(s_sensor, LINE_TOTAL_REG, REG_WORD_MASK);
    line->multiplier = s_sensor->get_reg(s_sensor, PLL_MULTIPLIER_REG, REG_BYTE_MASK);
    return line->line_total > 0 && line->multiplier > 0;
}

static int band_lines(const line_t *line)
{
    const int64_t sysclk_hz = (int64_t)GEN_VISION_XCLK_HZ * line->multiplier / PLL_DIVIDERS;
    return (int)(sysclk_hz * BAND_HALF_PERIOD_US / 1000000 / line->line_total);
}

// An exposure that is a whole number of mains half-periods keeps bands off the frame; the table's step suits
// 30 fps.
static esp_err_t fit_band_to_lines(const line_t *line)
{
    const int lines = band_lines(line);
    const bool wrote =
        s_sensor->set_reg(s_sensor, BAND_50HZ_STEP_REG, REG_BYTE_MASK, lines >> 8) >= 0 &&
        s_sensor->set_reg(s_sensor, BAND_50HZ_STEP_REG + 1, REG_BYTE_MASK, lines & REG_BYTE_MASK) >= 0;
    ESP_LOGI(TAG, "line total %d, PLL x%d, 10 ms is %d lines", line->line_total, line->multiplier, lines);
    return wrote ? ESP_OK : ESP_FAIL;
}

// AEC counts exposure in lines: carry the time, not the count, so it starts settled (KEHOACH 3.19).
static esp_err_t carry_exposure(int exposure, int gain, const line_t *before, const line_t *after)
{
    int64_t carried = (int64_t)exposure * before->line_total * after->multiplier /
                      ((int64_t)before->multiplier * after->line_total);
    const int64_t step = (int64_t)band_lines(after) * EXPOSURE_PER_LINE;
    // AEC rests only on whole 10 ms steps under the 50 Hz filter; between them it hunts (KEHOACH 3.19).
    if (step > 0 && carried >= step) { carried = (carried + step / 2) / step * step; }
    const bool wrote = s_sensor->set_reg(s_sensor, AEC_MANUAL_REG, AEC_AGC_MANUAL, AEC_AGC_MANUAL) >= 0 &&
                       s_sensor->set_reg(s_sensor, EXPOSURE_REG, EXPOSURE_MASK, (int)carried) >= 0 &&
                       s_sensor->set_reg(s_sensor, GAIN_REG, GAIN_MASK, gain) >= 0 &&
                       s_sensor->set_reg(s_sensor, AEC_MANUAL_REG, AEC_AGC_MANUAL, 0) >= 0;
    ESP_LOGI(TAG, "exposure %d -> %lld, gain %d", exposure, carried, gain);
    return wrote ? ESP_OK : ESP_FAIL;
}

esp_err_t drv_camera_init(void)
{
    if (s_sensor != NULL) { return ESP_ERR_INVALID_STATE; }
    const camera_config_t cfg = {
        .pin_pwdn = -1,
        .pin_reset = -1,
        .pin_xclk = APP_CAM_XCLK_GPIO,
        .pin_sccb_sda = APP_CAM_SIOD_GPIO,
        .pin_sccb_scl = APP_CAM_SIOC_GPIO,
        .pin_d7 = APP_CAM_D7_GPIO,
        .pin_d6 = APP_CAM_D6_GPIO,
        .pin_d5 = APP_CAM_D5_GPIO,
        .pin_d4 = APP_CAM_D4_GPIO,
        .pin_d3 = APP_CAM_D3_GPIO,
        .pin_d2 = APP_CAM_D2_GPIO,
        .pin_d1 = APP_CAM_D1_GPIO,
        .pin_d0 = APP_CAM_D0_GPIO,
        .pin_vsync = APP_CAM_VSYNC_GPIO,
        .pin_href = APP_CAM_HREF_GPIO,
        .pin_pclk = APP_CAM_PCLK_GPIO,
        .xclk_freq_hz = GEN_VISION_XCLK_HZ,
        .ledc_timer = LEDC_TIMER_0,
        .ledc_channel = LEDC_CHANNEL_0,
        .pixel_format = PIXFORMAT_JPEG,
        .frame_size = GEN_VISION_FRAME_SIZE,
        .jpeg_quality = GEN_VISION_JPEG_QUALITY,
        .fb_count = FB_COUNT,
        .fb_location = CAMERA_FB_IN_PSRAM,
        .grab_mode = CAMERA_GRAB_WHEN_EMPTY,
    };
    esp_err_t err = esp_camera_init(&cfg);
    if (err != ESP_OK) { return err; }
    s_sensor = esp_camera_sensor_get();
    if (s_sensor == NULL) { return ESP_ERR_NOT_FOUND; }
    if (s_sensor->set_vflip(s_sensor, GEN_VISION_VFLIP) != 0) { return ESP_FAIL; }
    s_can_focus = s_sensor->af_init != NULL && s_sensor->af_init(s_sensor, AF_LOAD_TIMEOUT_MS) == 0;
    ESP_LOGI(TAG, "sensor 0x%04X, %dx%d, autofocus %s", s_sensor->id.PID, GEN_VISION_WIDTH, GEN_VISION_HEIGHT,
             s_can_focus ? "loaded" : "off");
    line_t line;
    if (!read_line(&line)) { return ESP_FAIL; }
    err = fit_band_to_lines(&line);
    if (err != ESP_OK) { return err; }
    s_size = DRV_CAMERA_SIZE_DETECT;
    s_size_known = true;
    return drv_camera_rest();
}

esp_err_t drv_camera_use(drv_camera_size_t size)
{
    if (s_sensor == NULL) { return ESP_ERR_INVALID_STATE; }
    if (s_size_known && size == s_size) { return ESP_OK; }
    s_size_known = false;
    line_t before, after;
    const int exposure = s_sensor->get_reg(s_sensor, EXPOSURE_REG, EXPOSURE_MASK);
    const int gain = s_sensor->get_reg(s_sensor, GAIN_REG, GAIN_MASK);
    if (exposure < 0 || gain < 0 || !read_line(&before)) { return ESP_FAIL; }
    const bool full = size == DRV_CAMERA_SIZE_FULL;
    if (s_sensor->set_framesize(s_sensor, full ? GEN_VISION_FULL_FRAME_SIZE : GEN_VISION_FRAME_SIZE) != 0 ||
        s_sensor->set_quality(s_sensor, full ? GEN_VISION_FULL_SETTLE_QUALITY : GEN_VISION_JPEG_QUALITY) !=
            0 ||
        !read_line(&after)) {
        return ESP_FAIL;
    }
    // Each size has its own line length, so the 50 Hz step moves with it.
    esp_err_t err = fit_band_to_lines(&after);
    if (err == ESP_OK) { err = carry_exposure(exposure, gain, &before, &after); }
    if (err != ESP_OK) { return err; }
    s_size = size;
    s_size_known = true;
    return ESP_OK;
}

static uint32_t exposure_and_gain(void)
{
    const int exposure = s_sensor->get_reg(s_sensor, EXPOSURE_REG, REG_THREE_BYTES_MASK);
    const int gain = s_sensor->get_reg(s_sensor, GAIN_REG, REG_WORD_MASK);
    return ((uint32_t)exposure << 10) ^ (uint32_t)gain;
}

esp_err_t drv_camera_wake(void)
{
    if (s_sensor == NULL) { return ESP_ERR_INVALID_STATE; }
    if (s_sensor->set_reg(s_sensor, SYSTEM_CTRL0_REG, SYSTEM_CTRL0_STANDBY, 0) < 0) { return ESP_FAIL; }
    vTaskDelay(pdMS_TO_TICKS(WAKE_SETTLE_MS));
    // A dark room's noise takes a 5 MP picture at the best quality past the frame buffer (KEHOACH 3.19).
    if (full_size() && s_sensor->set_quality(s_sensor, GEN_VISION_FULL_SETTLE_QUALITY) != 0) {
        return ESP_FAIL;
    }
    const int64_t started_us = esp_timer_get_time();
    uint32_t last = 0;
    size_t last_bytes = 0;
    int still = 0;
    int frames = 0;
    // AEC stops inside its stable range, so an exposure that holds says the light has been found.
    while (frames < GEN_VISION_SETTLE_FRAMES_MAX &&
           (frames < GEN_VISION_SETTLE_FRAMES_MIN || still < GEN_VISION_STILL_FRAMES)) {
        camera_fb_t *frame = esp_camera_fb_get();
        if (frame == NULL) { return ESP_ERR_TIMEOUT; }
        last_bytes = frame->len;
        esp_camera_fb_return(frame);
        const uint32_t now = exposure_and_gain();
        still = now == last ? still + 1 : 0;
        last = now;
        frames++;
    }
    ESP_LOGI(TAG, "settled in %d frames, %lld ms, exposure %" PRIu32 " gain %" PRIu32, frames,
             (esp_timer_get_time() - started_us) / US_PER_MS, last >> 10, last & 0x3FF);
    if (!full_size()) { return ESP_OK; }
    s_full_quality = fitting_quality(last_bytes, GEN_VISION_FULL_SETTLE_QUALITY);
    if (s_sensor->set_quality(s_sensor, s_full_quality) != 0) { return ESP_FAIL; }
    ESP_LOGI(TAG, "quality %d for %u bytes at %d", s_full_quality, (unsigned)last_bytes,
             GEN_VISION_FULL_SETTLE_QUALITY);
    // The frame under way is still at the settle frames' quality.
    camera_fb_t *stale = esp_camera_fb_get();
    if (stale == NULL) { return ESP_ERR_TIMEOUT; }
    esp_camera_fb_return(stale);
    return ESP_OK;
}

esp_err_t drv_camera_focus(void)
{
    if (!s_can_focus) { return ESP_ERR_NOT_SUPPORTED; }
    if (s_sensor->af_trigger(s_sensor) != 0) { return ESP_FAIL; }
    const int64_t until_us = esp_timer_get_time() + (int64_t)GEN_VISION_FOCUS_TIMEOUT_MS * US_PER_MS;
    while (esp_timer_get_time() < until_us) {
        uint8_t raw = 0;
        bool focused = false, busy = true;
        s_sensor->af_get_status(s_sensor, &raw, &focused, &busy);
        if (focused) { return ESP_OK; }
        vTaskDelay(pdMS_TO_TICKS(AF_POLL_MS));
    }
    return ESP_ERR_TIMEOUT;
}

esp_err_t drv_camera_grab(drv_camera_frame_t *frame)
{
    // With one buffer the frame waiting is older than the focus that just ended.
    if (s_can_focus) { esp_camera_fb_return(esp_camera_fb_get()); }
    camera_fb_t *taken = esp_camera_fb_get();
    if (taken == NULL && full_size() && coarser_than(s_full_quality) != s_full_quality) {
        s_full_quality = coarser_than(s_full_quality);
        ESP_LOGW(TAG, "full frame lost, quality %d", s_full_quality);
        if (s_sensor->set_quality(s_sensor, s_full_quality) != 0) { return ESP_FAIL; }
        taken = esp_camera_fb_get();
    }
    if (taken == NULL) { return ESP_ERR_TIMEOUT; }
    const int quality = full_size() ? s_full_quality : GEN_VISION_JPEG_QUALITY;
    // A live view sets the next frame's quality from this one's size.
    if (full_size()) {
        const int next = fitting_quality(taken->len, s_full_quality);
        if (next != s_full_quality && s_sensor->set_quality(s_sensor, next) == 0) { s_full_quality = next; }
    }
    *frame = (drv_camera_frame_t){
        .jpeg = taken->buf,
        .bytes = taken->len,
        .width = (uint16_t)taken->width,
        .height = (uint16_t)taken->height,
        .quality = (uint8_t)quality,
        .held = taken,
    };
    return ESP_OK;
}

esp_err_t drv_camera_insert(drv_camera_frame_t *frame, const uint8_t *segment, size_t bytes)
{
    camera_fb_t *held = frame->held;
    if (held == NULL || held->len + bytes > CONFIG_CAMERA_JPEG_MODE_FRAME_SIZE) { return ESP_ERR_NO_MEM; }
    memmove(held->buf + JPEG_SOI_BYTES + bytes, held->buf + JPEG_SOI_BYTES, held->len - JPEG_SOI_BYTES);
    memcpy(held->buf + JPEG_SOI_BYTES, segment, bytes);
    held->len += bytes;
    frame->bytes = held->len;
    return ESP_OK;
}

void drv_camera_release(drv_camera_frame_t *frame)
{
    if (frame->held == NULL) { return; }
    esp_camera_fb_return(frame->held);
    frame->held = NULL;
}

esp_err_t drv_camera_rest(void)
{
    if (s_sensor == NULL) { return ESP_ERR_INVALID_STATE; }
    const int wrote =
        s_sensor->set_reg(s_sensor, SYSTEM_CTRL0_REG, SYSTEM_CTRL0_STANDBY, SYSTEM_CTRL0_STANDBY);
    return wrote < 0 ? ESP_FAIL : ESP_OK;
}

esp_err_t drv_camera_reg_get(uint16_t reg, uint8_t *value)
{
    if (s_sensor == NULL || value == NULL) { return ESP_ERR_INVALID_STATE; }
    const int got = s_sensor->get_reg(s_sensor, reg, REG_BYTE_MASK);
    if (got < 0) { return ESP_FAIL; }
    *value = (uint8_t)got;
    return ESP_OK;
}

esp_err_t drv_camera_reg_set(uint16_t reg, uint8_t value)
{
    if (s_sensor == NULL) { return ESP_ERR_INVALID_STATE; }
    return s_sensor->set_reg(s_sensor, reg, REG_BYTE_MASK, value) < 0 ? ESP_FAIL : ESP_OK;
}
