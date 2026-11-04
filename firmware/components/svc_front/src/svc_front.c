#include "svc_front.h"

#include <math.h>
#include <stdbool.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "gen_afe.h"
#include "gen_array.h"
#include "gen_grid.h"
#include "gen_tones.h"

#define DEAF_TAIL_HOPS (GEN_TONES_DEAF_TAIL_MS * 1000 / GEN_GRID_HOP_US)

static struct {
    dsp_afe_t *afe;
    uint32_t next_seq;
    uint32_t keep_hops;  // longest gap dsp_afe resumes over
    uint32_t deaf_until; // first seq whose vad counts again
    uint8_t n_mics;
    uint8_t n_channels;
    bool started;
} s_front;

// dsp_afe's input format by microphone count, then without and with the reference.
static const char *const kFormats[GEN_ARRAY_N_MICS][2] = {{"M", "MR"}, {"MM", "MMR"}};

esp_err_t svc_front_init(const svc_front_config_t *cfg)
{
    if (s_front.afe != NULL) { return ESP_ERR_INVALID_STATE; }
    if (cfg == NULL || cfg->n_mics == 0 || cfg->n_mics > GEN_ARRAY_N_MICS || cfg->n_channels < cfg->n_mics ||
        cfg->n_channels > cfg->n_mics + 1) {
        return ESP_ERR_INVALID_ARG;
    }
    const dsp_afe_config_t afe_cfg = {
        .input_format = kFormats[cfg->n_mics - 1][cfg->n_channels - cfg->n_mics],
        .spatial = DSP_AFE_SPATIAL_NONE,
        .calib = cfg->calib,
        .ns_floor_db = cfg->ns_floor_db,
        .agc_target_dbfs = cfg->agc_target_dbfs,
        .vad_aggressiveness = cfg->vad_aggressiveness,
    };
    size_t hot_bytes = 0;
    size_t cold_bytes = 0;
    esp_err_t err = dsp_afe_workspace_bytes(&afe_cfg, &hot_bytes, &cold_bytes);
    if (err != ESP_OK) { return err; }
    void *hot = heap_caps_malloc(hot_bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    void *cold = cold_bytes > 0 ? heap_caps_malloc(cold_bytes, MALLOC_CAP_SPIRAM) : NULL;
    if (hot == NULL || (cold_bytes > 0 && cold == NULL)) {
        heap_caps_free(hot);
        heap_caps_free(cold);
        return ESP_ERR_NO_MEM;
    }
    err = dsp_afe_init(&s_front.afe, &afe_cfg, hot, hot_bytes, cold, cold_bytes);
    if (err != ESP_OK) {
        s_front.afe = NULL;
        heap_caps_free(hot);
        heap_caps_free(cold);
    }
    const float keep_hops = GEN_AFE_CHAIN_GAP_KEEP_S * GEN_GRID_SAMPLE_RATE_HZ / GEN_GRID_HOP_SAMPLES;
    s_front.keep_hops = (uint32_t)lrintf(keep_hops);
    s_front.n_mics = cfg->n_mics;
    s_front.n_channels = cfg->n_channels;
    return err;
}

static bool speaker_sounds(const int16_t *interleaved)
{
    if (s_front.n_channels == s_front.n_mics) { return false; }
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        if (interleaved[i * s_front.n_channels + s_front.n_mics] != 0) { return true; }
    }
    return false;
}

esp_err_t svc_front_step(const int16_t *interleaved, uint32_t seq, dsp_afe_frame_t *out)
{
    if (s_front.afe == NULL) { return ESP_ERR_INVALID_STATE; }
    if (s_front.started && seq != s_front.next_seq) {
        // Unsigned: a seq that steps back reads as a gap longer than any kept, so it resets.
        const uint32_t missed = seq - s_front.next_seq;
        if (missed <= s_front.keep_hops) {
            dsp_afe_resume(s_front.afe);
        } else {
            dsp_afe_reset(s_front.afe);
        }
    }
    s_front.started = true;
    s_front.next_seq = seq + 1;
    // One hop in and straight out again, so the inner fifo never holds more than this one.
    const esp_err_t fed = dsp_afe_feed(s_front.afe, interleaved, 1);
    if (fed != ESP_OK) { return fed; }
    const esp_err_t fetched = dsp_afe_fetch(s_front.afe, out);
    if (fetched != ESP_OK) { return fetched; }
    out->seq = seq;
    if (speaker_sounds(interleaved)) { s_front.deaf_until = seq + DEAF_TAIL_HOPS; }
    // The microphone hears the speaker: its own beep is nobody's command (KEHOACH 3.19).
    if ((int32_t)(s_front.deaf_until - seq) > 0) { out->vad = 0; }
    return ESP_OK;
}

void svc_front_stats(dsp_afe_stats_t *out)
{
    if (s_front.afe == NULL) {
        memset(out, 0, sizeof(*out));
        return;
    }
    dsp_afe_stats(s_front.afe, out);
}
