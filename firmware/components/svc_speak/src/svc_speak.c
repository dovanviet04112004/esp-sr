#include "svc_speak.h"

#include <math.h>
#include <stdint.h>
#include <string.h>

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
#define NOISE_MULTIPLIER 1664525u // Numerical Recipes' LCG
#define NOISE_INCREMENT 1013904223u
#define NOISE_FULL_SCALE 2147483648.0f
#define WAV_HEADER_BYTES 44
#define WAV_FMT_BYTES 16
#define WAV_PCM 1
#define WAV_BITS 16
#define CLIP_HOPS 2 // a card read per two hops: each read opens the file again

typedef struct {
    uint16_t hz;
    uint8_t beeps;
    uint16_t beep_ms;
    uint16_t gap_ms;
} tone_t;

static const tone_t kTones[] = {
    [APP_TONE_NONE] = {0, 0, 0, 0},  [APP_TONE_WAKE] = GEN_TONE_WAKE, [APP_TONE_COMMAND] = GEN_TONE_COMMAND,
    [APP_TONE_DONE] = GEN_TONE_DONE, [APP_TONE_FAIL] = GEN_TONE_FAIL, [APP_TONE_TEST] = GEN_TONE_TEST,
};

static const char *TAG = "svc_speak";

static QueueHandle_t s_requests;
static svc_speak_read_t s_read;
static uint32_t s_noise = 1;

esp_err_t svc_speak_init(const svc_speak_config_t *cfg)
{
    if (cfg == NULL || cfg->requests == NULL) { return ESP_ERR_INVALID_ARG; }
    s_requests = cfg->requests;
    s_read = cfg->read;
    return ESP_OK;
}

static uint32_t samples_of(uint32_t ms)
{
    return ms * GEN_GRID_SAMPLE_RATE_HZ / MS_PER_S;
}

// A tone of 0 Hz is noise: aec learns the whole band from it, and a sine teaches it one bin.
static float wave(const tone_t *tone, float step, uint32_t at)
{
    if (tone->hz != 0) { return sinf(step * (float)at); }
    s_noise = s_noise * NOISE_MULTIPLIER + NOISE_INCREMENT;
    return (float)(int32_t)s_noise / NOISE_FULL_SCALE;
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
                sounding ? amplitude * envelope(at, total, ramp) * wave(tone, step, at) : 0.0f;
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

static uint32_t le(const uint8_t *p, size_t bytes)
{
    uint32_t value = 0;
    for (size_t i = bytes; i > 0; --i) {
        value = (value << 8) | p[i - 1];
    }
    return value;
}

// Only the canonical header svc_note writes: RIFF, a 16-byte PCM fmt chunk, then data.
static bool wav_fits(const uint8_t *h, uint32_t *pcm_bytes)
{
    *pcm_bytes = le(h + 40, 4);
    return memcmp(h, "RIFF", 4) == 0 && memcmp(h + 8, "WAVEfmt ", 8) == 0 && le(h + 16, 4) == WAV_FMT_BYTES &&
           le(h + 20, 2) == WAV_PCM && le(h + 22, 2) == 1 && le(h + 24, 4) == GEN_GRID_SAMPLE_RATE_HZ &&
           le(h + 34, 2) == WAV_BITS && memcmp(h + 36, "data", 4) == 0;
}

static esp_err_t play_clip(const char *path)
{
    if (s_read == NULL) { return ESP_ERR_NOT_SUPPORTED; }
    uint8_t header[WAV_HEADER_BYTES];
    size_t got = 0;
    uint32_t pcm_bytes = 0;
    esp_err_t err = s_read(path, 0, header, sizeof(header), &got);
    if (err == ESP_OK && (got != sizeof(header) || !wav_fits(header, &pcm_bytes))) {
        err = ESP_ERR_INVALID_RESPONSE;
    }
    int16_t block[CLIP_HOPS * GEN_GRID_HOP_SAMPLES];
    for (size_t done = 0; err == ESP_OK && done < pcm_bytes; done += got) {
        const size_t want = pcm_bytes - done < sizeof(block) ? pcm_bytes - done : sizeof(block);
        err = s_read(path, WAV_HEADER_BYTES + done, (uint8_t *)block, want, &got);
        if (err != ESP_OK || got < sizeof(int16_t)) { break; }
        err = drv_audio_write(block, got / sizeof(int16_t), WRITE_TIMEOUT_MS);
    }
    return err;
}

void svc_speak_step(void)
{
    app_speak_req_t request;
    if (xQueueReceive(s_requests, &request, pdMS_TO_TICKS(REQUEST_WAIT_MS)) != pdTRUE) { return; }
    if (request.tone != APP_TONE_NONE) {
        play_tone(request.tone);
    } else if (request.clip[0] != '\0') {
        const esp_err_t err = play_clip(request.clip);
        ESP_LOGI(TAG, "clip %s: %s", request.clip, esp_err_to_name(err));
    }
}
