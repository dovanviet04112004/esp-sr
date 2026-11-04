#include "svc_speak.h"

#include <math.h>
#include <stdint.h>

#include "app_events.h"
#include "drv_audio.h"
#include "esp_log.h"
#include "gen_grid.h"
#include "gen_tones.h"

#define REQUEST_WAIT_MS 1000
#define WRITE_TIMEOUT_MS 1000
#define MS_PER_S 1000
#define TWO_PI 6.28318530718f
#define DB_PER_DECADE 20.0f
#define INT16_FULL_SCALE 32767.0f

typedef struct {
    uint16_t hz;
    uint8_t beeps;
    uint16_t beep_ms;
    uint16_t gap_ms;
} tone_t;

static const tone_t kTones[] = {
    [APP_TONE_NONE] = {0, 0, 0, 0},  [APP_TONE_WAKE] = GEN_TONE_WAKE, [APP_TONE_COMMAND] = GEN_TONE_COMMAND,
    [APP_TONE_DONE] = GEN_TONE_DONE, [APP_TONE_FAIL] = GEN_TONE_FAIL,
};

static const char *TAG = "svc_speak";

static QueueHandle_t s_requests;

esp_err_t svc_speak_init(const svc_speak_config_t *cfg)
{
    if (cfg == NULL || cfg->requests == NULL) { return ESP_ERR_INVALID_ARG; }
    s_requests = cfg->requests;
    return ESP_OK;
}

static uint32_t samples_of(uint32_t ms)
{
    return ms * GEN_GRID_SAMPLE_RATE_HZ / MS_PER_S;
}

static float envelope(uint32_t at, uint32_t total, uint32_t ramp)
{
    const uint32_t edge = at < total - at ? at : total - at;
    return edge < ramp ? (float)edge / (float)ramp : 1.0f;
}

static esp_err_t play_samples(const tone_t *tone, uint32_t total, bool sounding)
{
    const float amplitude = INT16_FULL_SCALE * powf(10.0f, GEN_TONES_LEVEL_DBFS / DB_PER_DECADE);
    const float step = TWO_PI * (float)tone->hz / (float)GEN_GRID_SAMPLE_RATE_HZ;
    const uint32_t ramp = samples_of(GEN_TONES_RAMP_MS);
    int16_t block[GEN_GRID_HOP_SAMPLES];
    for (uint32_t done = 0; done < total;) {
        const uint32_t left = total - done;
        const uint32_t take = left < GEN_GRID_HOP_SAMPLES ? left : GEN_GRID_HOP_SAMPLES;
        for (uint32_t i = 0; i < take; ++i) {
            const uint32_t at = done + i;
            const float value =
                sounding ? amplitude * envelope(at, total, ramp) * sinf(step * (float)at) : 0.0f;
            block[i] = (int16_t)value;
        }
        const esp_err_t err = drv_audio_write(block, take, WRITE_TIMEOUT_MS);
        if (err != ESP_OK) { return err; }
        done += take;
    }
    return ESP_OK;
}

static void play_tone(app_tone_t id)
{
    const tone_t *tone = &kTones[id];
    esp_err_t err = ESP_OK;
    for (uint8_t beep = 0; beep < tone->beeps && err == ESP_OK; ++beep) {
        err = play_samples(tone, samples_of(tone->beep_ms), true);
        if (err == ESP_OK && beep + 1 < tone->beeps) {
            err = play_samples(tone, samples_of(tone->gap_ms), false);
        }
    }
    if (err != ESP_OK) { ESP_LOGW(TAG, "tone %d: %s", (int)id, esp_err_to_name(err)); }
}

void svc_speak_step(void)
{
    app_speak_req_t request;
    if (xQueueReceive(s_requests, &request, pdMS_TO_TICKS(REQUEST_WAIT_MS)) == pdTRUE &&
        request.tone != APP_TONE_NONE) {
        play_tone(request.tone);
    }
}
