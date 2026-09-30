#include <string.h>

#include "esp_afe_config.h"
#include "esp_afe_sr_models.h"
#include "esp_heap_caps.h"
#include "esp_ns.h"
#include "esp_nsn_iface.h"
#include "esp_nsn_models.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "gen_array.h"
#include "gen_grid.h"
#include "model_path.h"
#include "test_report.h"
#include "variants.h"

#define MODEL_PARTITION "model"
#define WEBRTC_LEVEL_MAX 2
#define WEBRTC_FRAME_SAMPLES (GEN_GRID_SAMPLE_RATE_HZ * NS_FRAME_LENGTH_MS / 1000)
// Far longer than the AFE takes on one chunk: a fetch that waits this long for nothing means it stalled.
#define FETCH_WAIT_MS 1000
// Chunks of silence fed past the end at most, to push out what the AFE still holds.
#define FLUSH_CHUNKS_MAX 64
// The S3's vector loads drop the low four address bits, so a net's buffers start 16-byte aligned.
#define NSN_ALIGN_BYTES 16

static srmodel_list_t *s_models;

esp_err_t espsr_models_load(void)
{
    if (s_models != NULL) { return ESP_OK; }
    s_models = esp_srmodel_init(MODEL_PARTITION);
    if (s_models == NULL || s_models->num == 0) { return ESP_ERR_NOT_FOUND; }
    for (int i = 0; i < s_models->num; i++) {
        test_report_line("model %s", s_models->model_name[i]);
    }
    return ESP_OK;
}

static afe_config_t *bss_config(void)
{
    afe_config_t *cfg = afe_config_init("MM", s_models, AFE_TYPE_SR, AFE_MODE_HIGH_PERF);
    if (cfg == NULL) { return NULL; }
    cfg->aec_init = false;
    cfg->se_init = true;
    cfg->ns_init = false;
    cfg->wakenet_init = false;
    cfg->agc_init = false;
    // A pinned output channel is the raw first mic; with no wake word, their vad picks the separated one.
    cfg->fixed_output_channel = false;
    cfg->fixed_first_channel = false;
    cfg->vad_init = true;
    cfg->vad_enable_channel_trigger = true;
    cfg->memory_alloc_mode = AFE_MEMORY_ALLOC_MORE_PSRAM;
    return afe_config_check(cfg);
}

static void bss_chunk(const int16_t *input, size_t samples, size_t fed, size_t chunk, int16_t *buf)
{
    const size_t n = fed >= samples ? 0 : (samples - fed < chunk ? samples - fed : chunk);
    memset(buf, 0, chunk * GEN_ARRAY_N_MICS * sizeof(int16_t));
    memcpy(buf, input + fed * GEN_ARRAY_N_MICS, n * GEN_ARRAY_N_MICS * sizeof(int16_t));
}

typedef struct {
    uint32_t empty; // fetches that came back without data
    uint32_t data;  // fetches that brought data
    int last_ret;   // ret_value of the last fetch that brought none
    int channels;   // raw_data_channels of the last fetch with data
} bss_fetches_t;

static esp_err_t take_output(const afe_fetch_result_t *res, int channel, size_t take, int16_t *out)
{
    if (channel == ESPSR_JOB_AFE_CHANNEL) {
        memcpy(out, res->data, take * sizeof(int16_t));
        return ESP_OK;
    }
    if (res->raw_data == NULL || channel < 0 || channel >= res->raw_data_channels) {
        return ESP_ERR_INVALID_ARG;
    }
    // raw_data interleaves its channels: data matches its channel 2 on every fetch (compare.md).
    for (size_t i = 0; i < take; i++) {
        out[i] = res->raw_data[i * (size_t)res->raw_data_channels + (size_t)channel];
    }
    return ESP_OK;
}

esp_err_t espsr_run_bss(const espsr_job_t *job, const espsr_job_variant_t *v, const int16_t *input,
                        int16_t *out, espsr_job_result_t *r)
{
    espsr_cost_t cost;
    espsr_cost_begin(&cost);
    afe_config_t *cfg = bss_config();
    if (cfg == NULL) { return ESP_ERR_NO_MEM; }
    const esp_afe_sr_iface_t *afe = esp_afe_handle_from_config(cfg);
    esp_afe_sr_data_t *data = afe != NULL ? afe->create_from_config(cfg) : NULL;
    if (data == NULL) {
        afe_config_free(cfg);
        return ESP_ERR_NO_MEM;
    }
    afe->print_pipeline(data);
    const size_t chunk = (size_t)afe->get_feed_chunksize(data);
    const size_t fetch_chunk = (size_t)afe->get_fetch_chunksize(data);
    // At most a quarter of the AFE's ring in flight: half of it overflows and drops the first chunks.
    const size_t window = (cfg->afe_ringbuf_size > 4 ? (size_t)cfg->afe_ringbuf_size / 4 : 1) * chunk;
    esp_err_t err = afe->get_feed_channel_num(data) == GEN_ARRAY_N_MICS &&
                            afe->get_samp_rate(data) == GEN_GRID_SAMPLE_RATE_HZ
                        ? ESP_OK
                        : ESP_ERR_NOT_SUPPORTED;
    int16_t *buf = heap_caps_malloc(chunk * GEN_ARRAY_N_MICS * sizeof(int16_t), MALLOC_CAP_SPIRAM);
    err = err == ESP_OK && buf == NULL ? ESP_ERR_NO_MEM : err;
    espsr_cost_memory(&cost, r);
    espsr_cost_start(&cost);
    const size_t samples = job->samples;
    const size_t fed_max = samples + FLUSH_CHUNKS_MAX * chunk;
    size_t fed = 0;
    size_t got = 0;
    bss_fetches_t fetches = {0};
    while (err == ESP_OK && got < samples) {
        if (fed - got + chunk <= window && fed < fed_max) {
            bss_chunk(input, samples, fed, chunk, buf);
            afe->feed(data, buf);
            fed += chunk;
            continue;
        }
        const int64_t started_us = esp_timer_get_time();
        afe_fetch_result_t *res = afe->fetch_with_delay(data, pdMS_TO_TICKS(FETCH_WAIT_MS));
        if (res == NULL || res->ret_value != ESP_OK || res->data == NULL || res->data_size <= 0) {
            // Fetching only happens with the window full or the input spent, so an empty wait means a stall.
            fetches.empty++;
            fetches.last_ret = res != NULL ? res->ret_value : ESP_FAIL;
            err = ESP_ERR_TIMEOUT;
            continue;
        }
        espsr_cost_call(&cost, esp_timer_get_time() - started_us);
        fetches.data++;
        const size_t n = (size_t)res->data_size / sizeof(int16_t);
        const size_t take = samples - got < n ? samples - got : n;
        err = take_output(res, v->channel, take, out + got);
        got += take;
        r->channel = res->trigger_channel_id;
        fetches.channels = res->raw_data_channels;
    }
    // The AFE works in its own task, so a fetch's wait is not its work: no peak, the mean alone.
    espsr_cost_end(&cost, samples, 0, r);
    test_report_line(
        "bss feed=%u fetch=%u ring=%d fetches=%lu empty=%lu last_ret=%d fed=%u got=%u raw_channels=%d",
        (unsigned)chunk, (unsigned)fetch_chunk, cfg->afe_ringbuf_size, (unsigned long)fetches.data,
        (unsigned long)fetches.empty, fetches.last_ret, (unsigned)fed, (unsigned)got, fetches.channels);
    r->samples = err == ESP_OK ? (uint32_t)samples : 0;
    heap_caps_free(buf);
    afe->destroy(data);
    afe_config_free(cfg);
    return err;
}

esp_err_t espsr_run_webrtc(const espsr_job_variant_t *v, const int16_t *mono, size_t samples, int16_t *out,
                           espsr_job_result_t *r)
{
    if (v->level < 0 || v->level > WEBRTC_LEVEL_MAX) { return ESP_ERR_INVALID_ARG; }
    espsr_cost_t cost;
    espsr_cost_begin(&cost);
    ns_handle_t ns = ns_pro_create(NS_FRAME_LENGTH_MS, v->level, GEN_GRID_SAMPLE_RATE_HZ);
    if (ns == NULL) { return ESP_ERR_NO_MEM; }
    espsr_cost_memory(&cost, r);
    espsr_cost_start(&cost);
    int16_t in_frame[WEBRTC_FRAME_SAMPLES];
    int16_t out_frame[WEBRTC_FRAME_SAMPLES];
    for (size_t at = 0; at < samples; at += WEBRTC_FRAME_SAMPLES) {
        const size_t n = samples - at < WEBRTC_FRAME_SAMPLES ? samples - at : WEBRTC_FRAME_SAMPLES;
        memset(in_frame, 0, sizeof(in_frame));
        memcpy(in_frame, mono + at, n * sizeof(int16_t));
        const int64_t started_us = esp_timer_get_time();
        ns_process(ns, in_frame, out_frame);
        espsr_cost_call(&cost, esp_timer_get_time() - started_us);
        memcpy(out + at, out_frame, n * sizeof(int16_t));
    }
    espsr_cost_end(&cost, samples, WEBRTC_FRAME_SAMPLES, r);
    r->samples = (uint32_t)samples;
    ns_destroy(ns);
    return ESP_OK;
}

esp_err_t espsr_run_nsnet(const espsr_job_variant_t *v, const int16_t *mono, size_t samples, int16_t *out,
                          espsr_job_result_t *r)
{
    char name[ESPSR_JOB_MODEL_BYTES + 1] = {0};
    memcpy(name, v->model, ESPSR_JOB_MODEL_BYTES);
    if (espsr_models_load() != ESP_OK || esp_srmodel_exists(s_models, name) < 0) { return ESP_ERR_NOT_FOUND; }
    const esp_nsn_iface_t *nsn = esp_nsnet_handle_from_name(name);
    if (nsn == NULL) { return ESPSR_JOB_REFUSED; }
    espsr_cost_t cost;
    espsr_cost_begin(&cost);
    esp_nsn_data_t *model = nsn->create(name);
    if (model == NULL) { return ESP_ERR_NO_MEM; }
    const size_t chunk = (size_t)nsn->get_samp_chunksize(model);
    esp_err_t err = nsn->get_samp_rate(model) == GEN_GRID_SAMPLE_RATE_HZ ? ESP_OK : ESP_ERR_NOT_SUPPORTED;
    int16_t *in_chunk =
        heap_caps_aligned_alloc(NSN_ALIGN_BYTES, chunk * sizeof(int16_t), MALLOC_CAP_INTERNAL);
    int16_t *out_chunk =
        heap_caps_aligned_alloc(NSN_ALIGN_BYTES, chunk * sizeof(int16_t), MALLOC_CAP_INTERNAL);
    err = err == ESP_OK && (in_chunk == NULL || out_chunk == NULL) ? ESP_ERR_NO_MEM : err;
    espsr_cost_memory(&cost, r);
    espsr_cost_start(&cost);
    for (size_t at = 0; err == ESP_OK && at < samples; at += chunk) {
        const size_t n = samples - at < chunk ? samples - at : chunk;
        memset(in_chunk, 0, chunk * sizeof(int16_t));
        memcpy(in_chunk, mono + at, n * sizeof(int16_t));
        const int64_t started_us = esp_timer_get_time();
        const int ret = nsn->process(model, in_chunk, out_chunk);
        espsr_cost_call(&cost, esp_timer_get_time() - started_us);
        err = ret == 0 ? ESP_OK : ESP_FAIL;
        memcpy(out + at, out_chunk, n * sizeof(int16_t));
    }
    espsr_cost_end(&cost, samples, chunk, r);
    test_report_line("nsnet %s chunk=%u rate=%d", name, (unsigned)chunk, nsn->get_samp_rate(model));
    r->samples = err == ESP_OK ? (uint32_t)samples : 0;
    heap_caps_free(in_chunk);
    heap_caps_free(out_chunk);
    nsn->destroy(model);
    return err;
}
