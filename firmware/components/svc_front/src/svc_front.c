#include "svc_front.h"

#include <math.h>
#include <stdbool.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "gen_afe.h"
#include "gen_array.h"
#include "gen_grid.h"

static struct {
    dsp_afe_t *afe;
    uint32_t next_seq;
    uint32_t keep_hops; // longest gap dsp_afe resumes over
    bool started;
} s_front;

esp_err_t svc_front_init(const svc_front_config_t *cfg)
{
    if (s_front.afe != NULL) { return ESP_ERR_INVALID_STATE; }
    if (cfg == NULL || cfg->n_channels < GEN_ARRAY_N_MICS || cfg->n_channels > GEN_ARRAY_N_MICS + 1) {
        return ESP_ERR_INVALID_ARG;
    }
    const dsp_afe_config_t afe_cfg = {
        .input_format = cfg->n_channels > GEN_ARRAY_N_MICS ? "MMR" : "MM",
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
    return err;
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
    if (fetched == ESP_OK) { out->seq = seq; }
    return fetched;
}

void svc_front_stats(dsp_afe_stats_t *out)
{
    if (s_front.afe == NULL) {
        memset(out, 0, sizeof(*out));
        return;
    }
    dsp_afe_stats(s_front.afe, out);
}
