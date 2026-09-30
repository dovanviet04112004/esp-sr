#include <string.h>

#include "dsp_afe.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "gen_afe.h"
#include "gen_array.h"
#include "gen_grid.h"
#include "variants.h"

// dsp_afe reads a zero-byte state as a module refusing its configuration, so the unity slot asks for one
// word.
static size_t unity_state_bytes(void *ctx)
{
    (void)ctx;
    return sizeof(uint32_t);
}

static esp_err_t unity_init(void *ctx, void *state, size_t bytes)
{
    (void)ctx;
    (void)state;
    (void)bytes;
    return ESP_OK;
}

static esp_err_t unity_process(void *ctx, void *state, const float *power, const float *echo_power,
                               float *gain, float *speech_prob)
{
    (void)ctx;
    (void)state;
    (void)power;
    (void)echo_power;
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        gain[k] = 1.0f;
    }
    *speech_prob = 1.0f;
    return ESP_OK;
}

// The ns slot with every gain at 1: the variants without noise suppression while ns_omlsa is built.
static const dsp_afe_ns_ops_t kUnityNs = {
    .state_bytes = unity_state_bytes,
    .init = unity_init,
    .process = unity_process,
};

esp_err_t espsr_run_dsp_afe(const espsr_job_t *job, const espsr_job_variant_t *v, const int16_t *input,
                            int16_t *out, espsr_job_result_t *r)
{
    static dsp_afe_calib_t calib;
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        calib.balance[k] = (dsp_spec_cplx_t){job->balance[2 * k], job->balance[2 * k + 1]};
    }
    const dsp_afe_config_t cfg = {
        .input_format = "MM",
        .spatial = (dsp_afe_spatial_t)v->spatial,
        .ns = v->ns_on ? NULL : &kUnityNs,
        .calib = &calib,
        .ns_floor_db = v->ns_floor_db,
        .agc_target_dbfs = GEN_AFE_AGC_TARGET_DBFS,
        .vad_aggressiveness = GEN_AFE_VAD_AGGRESSIVENESS,
    };
    espsr_cost_t cost;
    espsr_cost_begin(&cost);
    size_t hot_bytes = 0;
    size_t cold_bytes = 0;
    esp_err_t err = dsp_afe_workspace_bytes(&cfg, &hot_bytes, &cold_bytes);
    if (err != ESP_OK) { return err; }
    void *hot = heap_caps_malloc(hot_bytes, MALLOC_CAP_INTERNAL);
    void *cold = cold_bytes > 0 ? heap_caps_malloc(cold_bytes, MALLOC_CAP_SPIRAM) : NULL;
    dsp_afe_t *afe = NULL;
    err = hot == NULL || (cold_bytes > 0 && cold == NULL)
              ? ESP_ERR_NO_MEM
              : dsp_afe_init(&afe, &cfg, hot, hot_bytes, cold, cold_bytes);
    espsr_cost_memory(&cost, r);
    espsr_cost_start(&cost);
    const size_t samples = job->samples;
    int16_t hop[GEN_GRID_HOP_SAMPLES * GEN_ARRAY_N_MICS];
    dsp_afe_frame_t frame;
    for (size_t at = 0; err == ESP_OK && at < samples; at += GEN_GRID_HOP_SAMPLES) {
        const size_t n = samples - at < GEN_GRID_HOP_SAMPLES ? samples - at : GEN_GRID_HOP_SAMPLES;
        memset(hop, 0, sizeof(hop));
        memcpy(hop, input + at * GEN_ARRAY_N_MICS, n * GEN_ARRAY_N_MICS * sizeof(int16_t));
        const int64_t started_us = esp_timer_get_time();
        err = dsp_afe_feed(afe, hop, 1);
        if (err == ESP_OK) { err = dsp_afe_fetch(afe, &frame); }
        espsr_cost_call(&cost, esp_timer_get_time() - started_us);
        if (err == ESP_OK) { memcpy(out + at, frame.pcm, n * sizeof(int16_t)); }
    }
    espsr_cost_end(&cost, samples, GEN_GRID_HOP_SAMPLES, r);
    r->samples = err == ESP_OK ? (uint32_t)samples : 0;
    heap_caps_free(hot);
    heap_caps_free(cold);
    return err;
}
