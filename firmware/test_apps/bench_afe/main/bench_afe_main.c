#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dsp_afe.h"
#include "dsp_afe/agc.h"
#include "dsp_afe/balance.h"
#include "dsp_afe/hpf.h"
#include "dsp_afe/ns.h"
#include "dsp_afe/vad.h"
#include "dsp_spec/fft.h"
#include "dsp_spec/mel.h"
#include "dsp_spec/stft.h"
#include "esp_app_desc.h"
#include "esp_cpu.h"
#include "esp_heap_caps.h"
#include "esp_rom_sys.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gen_afe.h"
#include "gen_array.h"
#include "gen_grid.h"
#include "test_report.h"

#define WARMUP_HOPS 16
#define TIMED_HOPS 2000
#define BENCH_STACK_BYTES 8192
#define BENCH_PRIORITY 20
#define CORE_SACH 1 // sach_task runs dsp_afe (KEHOACH 5.2)
#define CORE_NHAN 0 // nhan_task computes log-mel (KEHOACH 5.2)
#define PCM_FULL_SCALE 32768.0f
#define REPORT_LINES_MAX 64
#define NS_LEVEL_HOPS 40 // quiet and loud stretches reach every branch
#define NS_LOUD 100.0f
// The 40-band case of contracts/golden/mel until E11 fixes the recogniser's front end.
#define MEL_BANDS 40
#define MEL_F_MIN_HZ 20.0f
#define MEL_F_MAX_HZ 7600.0f
#define MEL_LOG_FLOOR 1e-6f

typedef struct {
    const char *module;
    int core;
    size_t static_bytes; // allocated by the module itself at init
    size_t hot_bytes;
    size_t cold_bytes;
    bool in_total; // false for a part of another row
} row_t;

typedef struct {
    uint64_t sum_cycles;
    uint32_t peak_cycles;
} timing_t;

static float s_hop[GEN_ARRAY_N_MICS][GEN_GRID_HOP_SAMPLES];
static int16_t s_interleaved[GEN_GRID_HOP_SAMPLES * GEN_ARRAY_N_MICS];
static dsp_spec_cplx_t s_bins[GEN_ARRAY_N_MICS][GEN_GRID_N_BINS];
static dsp_spec_cplx_t s_work[GEN_GRID_N_BINS];
static float s_work_pcm[GEN_GRID_HOP_SAMPLES];
static dsp_afe_calib_t s_calib;
static float s_log_mel[MEL_BANDS];
static float s_power[GEN_GRID_N_BINS];
static float s_gain[GEN_GRID_N_BINS];
static uint32_t s_rng = 0x2545F491u;

static float noise(void)
{
    s_rng ^= s_rng << 13;
    s_rng ^= s_rng >> 17;
    s_rng ^= s_rng << 5;
    return (float)(s_rng >> 8) / (float)(1u << 24) - 0.5f;
}

static void fill_inputs(void)
{
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        for (size_t m = 0; m < GEN_ARRAY_N_MICS; m++) {
            s_hop[m][i] = 0.25f * noise();
            s_interleaved[i * GEN_ARRAY_N_MICS + m] = (int16_t)lrintf(s_hop[m][i] * PCM_FULL_SCALE);
        }
    }
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        s_calib.balance[k] = (dsp_spec_cplx_t){noise(), noise()};
    }
}

static size_t heap_free(void)
{
    return heap_caps_get_free_size(MALLOC_CAP_DEFAULT);
}

static void timing_add(timing_t *t, uint32_t start)
{
    const uint32_t cycles = esp_cpu_get_cycle_count() - start;
    t->sum_cycles += cycles;
    t->peak_cycles = cycles > t->peak_cycles ? cycles : t->peak_cycles;
}

static void report(const row_t *row, const timing_t *t)
{
    const float per_us = (float)esp_rom_get_cpu_ticks_per_us();
    test_report_line("csv %s,%d,%u,%u,%u,%.1f,%.1f,%d,%s", row->module, row->core,
                     (unsigned)row->static_bytes, (unsigned)row->hot_bytes, (unsigned)row->cold_bytes,
                     (double)(t->sum_cycles / (float)TIMED_HOPS / per_us), (double)(t->peak_cycles / per_us),
                     row->in_total ? 1 : 0, esp_app_get_description()->version);
}

static dsp_spec_fft_t *make_fft(size_t *self_bytes)
{
    const size_t bytes = dsp_spec_fft_workspace_bytes(GEN_GRID_FFT_SIZE);
    void *mem = heap_caps_malloc(bytes, MALLOC_CAP_INTERNAL);
    const size_t before = heap_free();
    dsp_spec_fft_t *fft = NULL;
    ESP_ERROR_CHECK(dsp_spec_fft_init(&fft, GEN_GRID_FFT_SIZE, mem, bytes));
    *self_bytes = before - heap_free();
    return fft;
}

static void bench_stft(void)
{
    row_t row = {.module = "dsp_spec stft (2 kênh)", .core = CORE_SACH, .in_total = false};
    dsp_spec_fft_t *fft = make_fft(&row.static_bytes);
    dsp_spec_stft_t *st[GEN_ARRAY_N_MICS];
    row.hot_bytes = dsp_spec_fft_workspace_bytes(GEN_GRID_FFT_SIZE);
    for (size_t m = 0; m < GEN_ARRAY_N_MICS; m++) {
        const size_t bytes = dsp_spec_stft_workspace_bytes();
        ESP_ERROR_CHECK(dsp_spec_stft_init(&st[m], fft, heap_caps_malloc(bytes, MALLOC_CAP_INTERNAL), bytes));
        row.hot_bytes += bytes;
    }
    timing_t t = {0};
    for (int h = 0; h < WARMUP_HOPS + TIMED_HOPS; h++) {
        const uint32_t start = esp_cpu_get_cycle_count();
        for (size_t m = 0; m < GEN_ARRAY_N_MICS; m++) {
            dsp_spec_stft_analyze(st[m], s_hop[m], s_bins[m]);
        }
        if (h >= WARMUP_HOPS) { timing_add(&t, start); }
    }
    report(&row, &t);
}

static void bench_istft(void)
{
    row_t row = {.module = "dsp_spec istft", .core = CORE_SACH, .in_total = false};
    dsp_spec_fft_t *fft = make_fft(&row.static_bytes);
    const size_t bytes = dsp_spec_istft_workspace_bytes();
    dsp_spec_istft_t *st = NULL;
    ESP_ERROR_CHECK(dsp_spec_istft_init(&st, fft, heap_caps_malloc(bytes, MALLOC_CAP_INTERNAL), bytes));
    row.hot_bytes = dsp_spec_fft_workspace_bytes(GEN_GRID_FFT_SIZE) + bytes;
    static float out[GEN_GRID_HOP_SAMPLES];
    timing_t t = {0};
    for (int h = 0; h < WARMUP_HOPS + TIMED_HOPS; h++) {
        const uint32_t start = esp_cpu_get_cycle_count();
        dsp_spec_istft_synthesize(st, s_bins[0], out);
        if (h >= WARMUP_HOPS) { timing_add(&t, start); }
    }
    report(&row, &t);
}

static void bench_hpf(void)
{
    const dsp_afe_hpf_config_t cfg = {.cutoff_hz = GEN_AFE_HPF_CUTOFF_HZ, .n_channels = GEN_ARRAY_N_MICS};
    row_t row = {.module = "dsp_afe hpf (2 kênh)", .core = CORE_SACH, .in_total = false};
    row.hot_bytes = dsp_afe_hpf_workspace_bytes(&cfg);
    void *mem = heap_caps_malloc(row.hot_bytes, MALLOC_CAP_INTERNAL);
    const size_t before = heap_free();
    dsp_afe_hpf_t *hpf = NULL;
    ESP_ERROR_CHECK(dsp_afe_hpf_init(&hpf, &cfg, mem, row.hot_bytes));
    row.static_bytes = before - heap_free();
    timing_t t = {0};
    for (int h = 0; h < WARMUP_HOPS + TIMED_HOPS; h++) {
        const uint32_t start = esp_cpu_get_cycle_count();
        for (uint8_t m = 0; m < GEN_ARRAY_N_MICS; m++) {
            dsp_afe_hpf_process(hpf, m, s_hop[m], GEN_GRID_HOP_SAMPLES);
        }
        if (h >= WARMUP_HOPS) { timing_add(&t, start); }
    }
    report(&row, &t);
}

static void bench_balance(void)
{
    row_t row = {.module = "dsp_afe balance", .core = CORE_SACH, .in_total = false};
    row.hot_bytes = sizeof(s_calib.balance);
    timing_t t = {0};
    for (int h = 0; h < WARMUP_HOPS + TIMED_HOPS; h++) {
        memcpy(s_work, s_bins[1], sizeof(s_work));
        const uint32_t start = esp_cpu_get_cycle_count();
        dsp_afe_balance_apply(s_calib.balance, s_work);
        if (h >= WARMUP_HOPS) { timing_add(&t, start); }
    }
    report(&row, &t);
}

static void bench_vad(void)
{
    const dsp_afe_vad_config_t cfg = {.aggressiveness = 0, .hangover_ms = GEN_AFE_VAD_HANGOVER_MS};
    row_t row = {.module = "dsp_afe vad", .core = CORE_SACH, .in_total = false};
    row.hot_bytes = dsp_afe_vad_workspace_bytes(&cfg);
    void *mem = heap_caps_malloc(row.hot_bytes, MALLOC_CAP_INTERNAL);
    const size_t before = heap_free();
    dsp_afe_vad_t *vad = NULL;
    ESP_ERROR_CHECK(dsp_afe_vad_init(&vad, &cfg, mem, row.hot_bytes));
    row.static_bytes = before - heap_free();
    bool speech = false;
    timing_t t = {0};
    for (int h = 0; h < WARMUP_HOPS + TIMED_HOPS; h++) {
        const uint32_t start = esp_cpu_get_cycle_count();
        ESP_ERROR_CHECK(dsp_afe_vad_process(vad, s_hop[0], &speech));
        if (h >= WARMUP_HOPS) { timing_add(&t, start); }
    }
    report(&row, &t);
}

static void bench_ns(void)
{
    dsp_afe_ns_omlsa_config_t cfg = {.floor_db = GEN_AFE_NS_FLOOR_DB};
    const dsp_afe_ns_ops_t *ns = dsp_afe_ns_omlsa_ops();
    row_t row = {.module = "dsp_afe ns_omlsa", .core = CORE_SACH, .in_total = false};
    row.hot_bytes = ns->state_bytes(&cfg);
    void *mem = heap_caps_malloc(row.hot_bytes, MALLOC_CAP_INTERNAL);
    const size_t before = heap_free();
    ESP_ERROR_CHECK(ns->init(&cfg, mem, row.hot_bytes));
    row.static_bytes = before - heap_free();
    float speech_prob = 0.0f;
    timing_t t = {0};
    for (int h = 0; h < WARMUP_HOPS + TIMED_HOPS; h++) {
        const float level = (h / NS_LEVEL_HOPS) % 2 ? NS_LOUD : 1.0f;
        for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
            const float x = noise();
            s_power[k] = level * x * x;
        }
        const uint32_t start = esp_cpu_get_cycle_count();
        ESP_ERROR_CHECK(ns->process(&cfg, mem, s_power, NULL, s_gain, &speech_prob));
        if (h >= WARMUP_HOPS) { timing_add(&t, start); }
    }
    report(&row, &t);
}

// limiting: a 0 dBFS target drives the bench noise into the ceiling, so the limiter works on every sample.
static void bench_agc(bool limiting)
{
    const dsp_afe_agc_config_t cfg = {
        .target_dbfs = limiting ? 0.0f : GEN_AFE_AGC_TARGET_DBFS,
        .gain_min_db = GEN_AFE_AGC_GAIN_MIN_DB,
        .gain_max_db = GEN_AFE_AGC_GAIN_MAX_DB,
        .up_db_per_s = GEN_AFE_AGC_UP_DB_PER_S,
        .down_db_per_s = GEN_AFE_AGC_DOWN_DB_PER_S,
        .limit_dbfs = GEN_AFE_AGC_LIMIT_DBFS,
        .lookahead_ms = GEN_AFE_AGC_LOOKAHEAD_MS,
        .level_tau_s = GEN_AFE_AGC_LEVEL_TAU_S,
        .level_gate_db = GEN_AFE_AGC_LEVEL_GATE_DB,
        .level_fall_db_per_s = GEN_AFE_AGC_LEVEL_FALL_DB_PER_S,
        .release_ms = GEN_AFE_AGC_RELEASE_MS,
    };
    row_t row = {.module = limiting ? "dsp_afe agc chặn đỉnh mọi mẫu" : "dsp_afe agc", .core = CORE_SACH};
    row.hot_bytes = dsp_afe_agc_workspace_bytes(&cfg);
    void *mem = heap_caps_malloc(row.hot_bytes, MALLOC_CAP_INTERNAL);
    const size_t before = heap_free();
    dsp_afe_agc_t *agc = NULL;
    ESP_ERROR_CHECK(dsp_afe_agc_init(&agc, &cfg, mem, row.hot_bytes));
    row.static_bytes = before - heap_free();
    float gain_db = 0.0f;
    timing_t t = {0};
    for (int h = 0; h < WARMUP_HOPS + TIMED_HOPS; h++) {
        memcpy(s_work_pcm, s_hop[0], sizeof(s_work_pcm));
        const uint32_t start = esp_cpu_get_cycle_count();
        ESP_ERROR_CHECK(dsp_afe_agc_process(agc, s_work_pcm, true, &gain_db));
        if (h >= WARMUP_HOPS) { timing_add(&t, start); }
    }
    report(&row, &t);
}

static void bench_chain(void)
{
    const dsp_afe_config_t cfg = {.input_format = "MM",
                                  .spatial = DSP_AFE_SPATIAL_NONE,
                                  .calib = &s_calib,
                                  .ns_floor_db = GEN_AFE_NS_FLOOR_DB,
                                  .agc_target_dbfs = GEN_AFE_AGC_TARGET_DBFS,
                                  .vad_aggressiveness = GEN_AFE_VAD_AGGRESSIVENESS};
    row_t row = {.module = "dsp_afe chuỗi (hpf + stft x2 + balance + trộn + ns + istft + vad + agc)",
                 .core = CORE_SACH,
                 .in_total = true};
    ESP_ERROR_CHECK(dsp_afe_workspace_bytes(&cfg, &row.hot_bytes, &row.cold_bytes));
    void *hot = heap_caps_malloc(row.hot_bytes, MALLOC_CAP_INTERNAL);
    void *cold = row.cold_bytes > 0 ? heap_caps_malloc(row.cold_bytes, MALLOC_CAP_SPIRAM) : NULL;
    const size_t before = heap_free();
    dsp_afe_t *afe = NULL;
    ESP_ERROR_CHECK(dsp_afe_init(&afe, &cfg, hot, row.hot_bytes, cold, row.cold_bytes));
    row.static_bytes = before - heap_free();
    dsp_afe_frame_t frame;
    timing_t t = {0};
    for (int h = 0; h < WARMUP_HOPS + TIMED_HOPS; h++) {
        const uint32_t start = esp_cpu_get_cycle_count();
        ESP_ERROR_CHECK(dsp_afe_feed(afe, s_interleaved, 1));
        ESP_ERROR_CHECK(dsp_afe_fetch(afe, &frame));
        if (h >= WARMUP_HOPS) { timing_add(&t, start); }
    }
    report(&row, &t);
}

static void bench_mel(void)
{
    const dsp_spec_mel_config_t cfg = {
        .n_bands = MEL_BANDS, .f_min_hz = MEL_F_MIN_HZ, .f_max_hz = MEL_F_MAX_HZ, .log_floor = MEL_LOG_FLOOR};
    row_t row = {.module = "dsp_spec log-mel (40 dải)", .core = CORE_NHAN, .in_total = true};
    row.hot_bytes = dsp_spec_mel_workspace_bytes(&cfg);
    void *mem = heap_caps_malloc(row.hot_bytes, MALLOC_CAP_INTERNAL);
    const size_t before = heap_free();
    dsp_spec_mel_t *mel = NULL;
    ESP_ERROR_CHECK(dsp_spec_mel_init(&mel, &cfg, mem, row.hot_bytes));
    row.static_bytes = before - heap_free();
    timing_t t = {0};
    for (int h = 0; h < WARMUP_HOPS + TIMED_HOPS; h++) {
        const uint32_t start = esp_cpu_get_cycle_count();
        dsp_spec_mel_log(mel, s_bins[0], s_log_mel);
        if (h >= WARMUP_HOPS) { timing_add(&t, start); }
    }
    report(&row, &t);
}

// A tick between benches lets the idle task of the core feed the task watchdog.
// The chain goes first: dl_fft keeps its tables once made, so the first user of them pays their RAM.
static void core1_benches(void *done)
{
    bench_chain();
    vTaskDelay(1);
    bench_stft();
    vTaskDelay(1);
    bench_istft();
    vTaskDelay(1);
    bench_hpf();
    vTaskDelay(1);
    bench_balance();
    vTaskDelay(1);
    bench_ns();
    vTaskDelay(1);
    bench_vad();
    vTaskDelay(1);
    bench_agc(false);
    vTaskDelay(1);
    bench_agc(true);
    xTaskNotifyGive((TaskHandle_t)done);
    vTaskDelete(NULL);
}

static void core0_benches(void *done)
{
    bench_mel();
    xTaskNotifyGive((TaskHandle_t)done);
    vTaskDelete(NULL);
}

static void run_on(TaskFunction_t fn, const char *name, int core)
{
    xTaskCreatePinnedToCore(fn, name, BENCH_STACK_BYTES, xTaskGetCurrentTaskHandle(), BENCH_PRIORITY, NULL,
                            core);
    ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
}

void app_main(void)
{
    fill_inputs();
    if (!test_report_begin("BENCH", REPORT_LINES_MAX)) { return; }
    test_report_line("columns module,core,static_bytes,hot_bytes,cold_bytes,us_mean,us_peak,in_total,fw");
    run_on(core1_benches, "bench_core1", CORE_SACH);
    run_on(core0_benches, "bench_core0", CORE_NHAN);
    test_report_line("done");
    test_report_serve();
}
