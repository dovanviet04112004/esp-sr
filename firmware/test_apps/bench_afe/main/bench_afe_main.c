#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dsp_afe.h"
#include "dsp_afe/agc.h"
#include "dsp_afe/balance.h"
#include "dsp_afe/bss.h"
#include "dsp_afe/doa.h"
#include "dsp_afe/gsc.h"
#include "dsp_afe/hpf.h"
#include "dsp_afe/ns.h"
#include "dsp_afe/vad.h"
#include "dsp_spec/fft.h"
#include "dsp_spec/mel.h"
#include "dsp_spec/pitch.h"
#include "dsp_spec/spk_fbank.h"
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
#include "gen_listen.h"
#include "gen_speaker.h"
#include "lang_vi.h"
#include "test_report.h"

#define WARMUP_HOPS 16
#define TIMED_HOPS 2000
#define YIELD_EVERY_HOPS 100 // well under the 5 s of the task watchdog
#define BENCH_STACK_BYTES 8192
#define BENCH_PRIORITY 20
#define CORE_SACH 1 // sach_task runs dsp_afe (KEHOACH 5.2)
#define CORE_NHAN 0 // nhan_task computes log-mel (KEHOACH 5.2)
#define CORE_XAC 0
#define SPK_WARMUP_WINDOWS 1
#define SPK_TIMED_WINDOWS 20 // a window is ~0.1 s of work, not a hop's
#define PCM_FULL_SCALE 32768.0f
#define REPORT_LINES_MAX 64
#define NS_LEVEL_HOPS 40 // quiet and loud stretches reach every branch
#define NS_LOUD 100.0f
#define PITCH_VOICE_HOPS 64 // a gliding voiced tone in noise, cycled
#define PITCH_F0_HZ 140.0f
#define PITCH_GLIDE_HZ 60.0f
#define PITCH_HARMONICS 5
#define BENCH_STEER_DEG 60.0f
#define GSC_ANGLES 91       // the doa grid, 0..180 deg
#define BSS_TALKER_A_DEG 48 // board B's talkers of raw/device/tinyai
#define BSS_TALKER_B_DEG 130
#define BSS_TURN_HOPS 156 // 2.5 s turns: LEARN splits, then reclusters
#define ROW_NAME_BYTES 64
#if CONFIG_DSP_AFE_DOA_ENABLE
#define CHAIN_ROW "dsp_afe chuỗi (hpf + stft x2 + balance + doa + trộn + ns + istft + vad + agc)"
#else
#define CHAIN_ROW "dsp_afe chuỗi (hpf + stft x2 + balance + trộn + ns + istft + vad + agc)"
#endif
// A command with a number and a symbol: normalize, then g2p in each region, as nhan_task does on a new set.
#define LANG_LINE "t\xc4\x83ng \xc3\xa2m l\xc6\xb0\xe1\xbb\xa3ng l\xc3\xaan 50%"

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
    uint32_t runs;
} timing_t;

static float s_hop[GEN_ARRAY_N_MICS][GEN_GRID_HOP_SAMPLES];
static int16_t s_interleaved[GEN_GRID_HOP_SAMPLES * GEN_ARRAY_N_MICS];
static dsp_spec_cplx_t s_bins[GEN_ARRAY_N_MICS][GEN_GRID_N_BINS];
static dsp_spec_cplx_t s_work[GEN_GRID_N_BINS];
static float s_work_pcm[GEN_GRID_HOP_SAMPLES];
static dsp_afe_calib_t s_calib;
static float s_log_mel[GEN_LISTEN_N_BANDS];
static float s_voice[PITCH_VOICE_HOPS][GEN_GRID_HOP_SAMPLES];
static float s_pitch[DSP_SPEC_PITCH_FEATURES];
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
    t->runs++;
}

// Past the warm-up, add this hop's cycles; now and then give the core's idle task a tick, outside the timing.
static void hop_done(timing_t *t, int hop, uint32_t start)
{
    if (hop >= WARMUP_HOPS) { timing_add(t, start); }
    if (hop % YIELD_EVERY_HOPS == YIELD_EVERY_HOPS - 1) { vTaskDelay(1); }
}

static void report(const row_t *row, const timing_t *t)
{
    const float per_us = (float)esp_rom_get_cpu_ticks_per_us();
    test_report_line("csv \"%s\",%d,%u,%u,%u,%.1f,%.1f,%d,%s", row->module, row->core,
                     (unsigned)row->static_bytes, (unsigned)row->hot_bytes, (unsigned)row->cold_bytes,
                     (double)(t->sum_cycles / (float)t->runs / per_us), (double)(t->peak_cycles / per_us),
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
        hop_done(&t, h, start);
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
        hop_done(&t, h, start);
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
        hop_done(&t, h, start);
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
        hop_done(&t, h, start);
    }
    report(&row, &t);
}

#if CONFIG_DSP_AFE_DOA_ENABLE
// search: every hop searches the grid, the cost of a hop that doa updates on; otherwise the fold of every
// hop.
static void bench_doa(bool search)
{
    const dsp_afe_doa_config_t cfg = {
        .spacing_m = GEN_ARRAY_SPACING_M,
        .speed_of_sound_m_s = GEN_ARRAY_SPEED_OF_SOUND_M_S,
        .band_min_hz = GEN_AFE_DOA_BAND_MIN_HZ,
        .band_max_hz = GEN_AFE_DOA_BAND_MAX_HZ > 0.0f ? GEN_AFE_DOA_BAND_MAX_HZ : GEN_ARRAY_ALIAS_HZ,
        .grid_step_deg = GEN_AFE_DOA_GRID_STEP_DEG,
        .smooth_tau_s = GEN_AFE_DOA_SMOOTH_TAU_S,
    };
    row_t row = {.module = search ? "dsp_afe doa gộp và dò lưới" : "dsp_afe doa gộp phổ chéo",
                 .core = CORE_SACH};
    row.hot_bytes = dsp_afe_doa_workspace_bytes(&cfg);
    void *mem = heap_caps_malloc(row.hot_bytes, MALLOC_CAP_INTERNAL);
    const size_t before = heap_free();
    dsp_afe_doa_t *doa = NULL;
    ESP_ERROR_CHECK(dsp_afe_doa_init(&doa, &cfg, mem, row.hot_bytes));
    row.static_bytes = before - heap_free();
    dsp_afe_doa_result_t out;
    timing_t t = {0};
    for (int h = 0; h < WARMUP_HOPS + TIMED_HOPS; h++) {
        const uint32_t start = esp_cpu_get_cycle_count();
        ESP_ERROR_CHECK(dsp_afe_doa_process(doa, s_bins[0], s_bins[1], search, &out));
        hop_done(&t, h, start);
    }
    report(&row, &t);
}
#endif

#if CONFIG_DSP_AFE_GSC_ENABLE
// turning: the steer moves every hop, so the phasors are rebuilt each time, the worst doa can ask for.
static void bench_gsc(bool turning)
{
    const dsp_afe_gsc_config_t cfg = {
        .spacing_m = GEN_ARRAY_SPACING_M,
        .speed_of_sound_m_s = GEN_ARRAY_SPEED_OF_SOUND_M_S,
        .step_size = GEN_AFE_GSC_STEP_SIZE,
        .leakage = GEN_AFE_GSC_LEAKAGE,
        .weight_max = GEN_AFE_GSC_WEIGHT_MAX,
    };
    row_t row = {.module = turning ? "dsp_afe gsc học và đổi góc lái mỗi bước" : "dsp_afe gsc học",
                 .core = CORE_SACH};
    row.hot_bytes = dsp_afe_gsc_workspace_bytes(&cfg);
    void *mem = heap_caps_malloc(row.hot_bytes, MALLOC_CAP_INTERNAL);
    const size_t before = heap_free();
    dsp_afe_gsc_t *gsc = NULL;
    ESP_ERROR_CHECK(dsp_afe_gsc_init(&gsc, &cfg, mem, row.hot_bytes));
    row.static_bytes = before - heap_free();
    timing_t t = {0};
    for (int h = 0; h < WARMUP_HOPS + TIMED_HOPS; h++) {
        const float angle_deg =
            turning ? (float)(h % GSC_ANGLES) * GEN_AFE_DOA_GRID_STEP_DEG : BENCH_STEER_DEG;
        const uint32_t start = esp_cpu_get_cycle_count();
        ESP_ERROR_CHECK(dsp_afe_gsc_process(gsc, s_bins[0], s_bins[1], angle_deg, true, s_work));
        hop_done(&t, h, start);
    }
    report(&row, &t);
}
#endif

#if CONFIG_DSP_AFE_BSS_ENABLE
static dsp_spec_cplx_t s_rest[GEN_GRID_N_BINS];

// learn: the two talkers trade turns, so the peak hop is one where the seats are clustered again.
static void bench_bss(bool learn)
{
    const dsp_afe_bss_config_t cfg = {
        .mode = learn ? DSP_AFE_BSS_LEARN : DSP_AFE_BSS_FIXED,
        .keep_rule = (dsp_afe_bss_keep_t)GEN_AFE_BSS_KEEP_RULE,
        .keep_deg = GEN_AFE_BSS_KEEP_DEG,
        .gate_slope = GEN_AFE_BSS_GATE_SLOPE,
        .conf_min = GEN_AFE_BSS_CONF_MIN,
        .seats_max = GEN_AFE_BSS_SEATS_MAX,
        .decision_s = GEN_AFE_BSS_DECISION_S,
        .recheck_s = GEN_AFE_BSS_RECHECK_S,
        .first_speech_s = GEN_AFE_BSS_FIRST_SPEECH_S,
        .kmeans_rounds = GEN_AFE_BSS_KMEANS_ROUNDS,
        .confirm = GEN_AFE_BSS_CONFIRM,
        .occupancy_min = GEN_AFE_BSS_OCCUPANCY_MIN,
        .valley_max = GEN_AFE_BSS_VALLEY_MAX,
        .valley_width = GEN_AFE_BSS_VALLEY_WIDTH,
        .min_gap = GEN_AFE_BSS_MIN_GAP,
        .boundary_share_min = GEN_AFE_BSS_BOUNDARY_SHARE_MIN,
        .boundary_follow = GEN_AFE_BSS_BOUNDARY_FOLLOW,
        .track_tau_s = GEN_AFE_BSS_TRACK_TAU_S,
        .track_gate = GEN_AFE_BSS_TRACK_GATE,
        .seat_forget_s = GEN_AFE_BSS_SEAT_FORGET_S,
        .update_every_hops = GEN_AFE_DOA_UPDATE_EVERY_HOPS,
        .spacing_m = GEN_ARRAY_SPACING_M,
        .speed_of_sound_m_s = GEN_ARRAY_SPEED_OF_SOUND_M_S,
        .history_s = GEN_AFE_BSS_HISTORY_S,
    };
    row_t row = {.module = learn ? "dsp_afe bss LEARN, hai người đổi lượt" : "dsp_afe bss chia ở chính diện",
                 .core = CORE_SACH};
    row.hot_bytes = dsp_afe_bss_workspace_bytes(&cfg);
    void *mem = heap_caps_malloc(row.hot_bytes, MALLOC_CAP_INTERNAL);
    const size_t before = heap_free();
    dsp_afe_bss_t *bss = NULL;
    ESP_ERROR_CHECK(dsp_afe_bss_init(&bss, &cfg, mem, row.hot_bytes));
    row.static_bytes = before - heap_free();
    timing_t t = {0};
    for (int h = 0; h < WARMUP_HOPS + TIMED_HOPS; h++) {
        const int16_t talker = (h / BSS_TURN_HOPS) % 2 == 0 ? BSS_TALKER_A_DEG : BSS_TALKER_B_DEG;
        const int16_t angle = (int16_t)(talker + 2 * (h % 3 - 1));
        const bool searched = h % GEN_AFE_DOA_UPDATE_EVERY_HOPS == 0;
        const uint32_t start = esp_cpu_get_cycle_count();
        ESP_ERROR_CHECK(
            dsp_afe_bss_process(bss, s_bins[0], s_bins[1], angle, UINT8_MAX, searched, s_work, s_rest));
        hop_done(&t, h, start);
    }
    report(&row, &t);
}
#endif

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
        hop_done(&t, h, start);
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
        hop_done(&t, h, start);
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
        hop_done(&t, h, start);
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
    row_t row = {.module = CHAIN_ROW, .core = CORE_SACH, .in_total = true};
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
        hop_done(&t, h, start);
    }
    report(&row, &t);
}

static void bench_mel(void)
{
    const dsp_spec_mel_config_t cfg = GEN_LISTEN_MEL_CONFIG;
    char name[ROW_NAME_BYTES];
    snprintf(name, sizeof(name), "dsp_spec log-mel (%u dải)", (unsigned)cfg.n_bands);
    row_t row = {.module = name, .core = CORE_NHAN, .in_total = true};
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
        hop_done(&t, h, start);
    }
    report(&row, &t);
}

static void fill_voice(void)
{
    double phase = 0.0;
    const size_t n = (size_t)PITCH_VOICE_HOPS * GEN_GRID_HOP_SAMPLES;
    for (size_t i = 0; i < n; i++) {
        const double f0 = PITCH_F0_HZ + PITCH_GLIDE_HZ * sin(2.0 * M_PI * (double)i / (double)n);
        phase += 2.0 * M_PI * f0 / GEN_GRID_SAMPLE_RATE_HZ;
        double x = 0.0;
        for (int k = 1; k <= PITCH_HARMONICS; k++) {
            x += sin(k * phase) / k;
        }
        s_voice[i / GEN_GRID_HOP_SAMPLES][i % GEN_GRID_HOP_SAMPLES] = (float)(0.2 * x) + 0.02f * noise();
    }
}

static void bench_pitch(void)
{
    const dsp_spec_pitch_config_t cfg = GEN_LISTEN_PITCH_CONFIG;
    row_t row = {.module = "dsp_spec pitch Kaldi (vùng làm việc PSRAM)", .core = CORE_NHAN, .in_total = true};
    row.hot_bytes = dsp_spec_pitch_workspace_bytes(&cfg);
    void *mem = heap_caps_malloc(row.hot_bytes, MALLOC_CAP_SPIRAM);
    const size_t before = heap_free();
    dsp_spec_pitch_t *pitch = NULL;
    ESP_ERROR_CHECK(dsp_spec_pitch_init(&pitch, &cfg, mem, row.hot_bytes));
    row.static_bytes = before - heap_free();
    fill_voice();
    timing_t t = {0};
    for (int h = 0; h < WARMUP_HOPS + TIMED_HOPS; h++) {
        const uint32_t start = esp_cpu_get_cycle_count();
        dsp_spec_pitch_frame(pitch, s_voice[h % PITCH_VOICE_HOPS], s_pitch);
        hop_done(&t, h, start);
    }
    report(&row, &t);
    heap_caps_free(mem);
}

// fill_voice's tone as one window; workspace, features and PCM in PSRAM, as xac_task keeps them.
static void bench_spk_fbank(void)
{
    const dsp_spec_spk_fbank_config_t cfg = GEN_SPEAKER_FBANK_CONFIG;
    char name[ROW_NAME_BYTES];
    snprintf(name, sizeof(name), "dsp_spec spk_fbank (%u băng) một cửa sổ speaker", (unsigned)cfg.n_bands);
    row_t row = {.module = name, .core = CORE_XAC};
    const size_t work_bytes = dsp_spec_spk_fbank_workspace_bytes(&cfg);
    const size_t features_bytes = dsp_spec_spk_fbank_frames(&cfg) * cfg.n_bands * sizeof(float);
    row.cold_bytes = work_bytes + features_bytes;
    void *mem = heap_caps_malloc(work_bytes, MALLOC_CAP_SPIRAM);
    float *features = heap_caps_malloc(features_bytes, MALLOC_CAP_SPIRAM);
    int16_t *pcm = heap_caps_malloc(cfg.window_samples * sizeof(int16_t), MALLOC_CAP_SPIRAM);
    const size_t before = heap_free();
    dsp_spec_spk_fbank_t *fb = NULL;
    ESP_ERROR_CHECK(dsp_spec_spk_fbank_init(&fb, &cfg, mem, work_bytes));
    row.static_bytes = before - heap_free();
    fill_voice();
    for (size_t i = 0; i < cfg.window_samples; i++) {
        const float x = s_voice[(i / GEN_GRID_HOP_SAMPLES) % PITCH_VOICE_HOPS][i % GEN_GRID_HOP_SAMPLES];
        pcm[i] = (int16_t)lrintf(x * PCM_FULL_SCALE);
    }
    timing_t t = {0};
    for (int w = 0; w < SPK_WARMUP_WINDOWS + SPK_TIMED_WINDOWS; w++) {
        const uint32_t start = esp_cpu_get_cycle_count();
        ESP_ERROR_CHECK(dsp_spec_spk_fbank_run(fb, pcm, features));
        if (w >= SPK_WARMUP_WINDOWS) { timing_add(&t, start); }
        vTaskDelay(1);
    }
    report(&row, &t);
    heap_caps_free(mem);
    heap_caps_free(features);
    heap_caps_free(pcm);
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
#if CONFIG_DSP_AFE_GSC_ENABLE
    bench_gsc(false);
    vTaskDelay(1);
    bench_gsc(true);
    vTaskDelay(1);
#endif
#if CONFIG_DSP_AFE_BSS_ENABLE
    bench_bss(false);
    vTaskDelay(1);
    bench_bss(true);
    vTaskDelay(1);
#endif
#if CONFIG_DSP_AFE_DOA_ENABLE
    bench_doa(false);
    vTaskDelay(1);
    bench_doa(true);
    vTaskDelay(1);
#endif
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

static void bench_lang(void)
{
    row_t row = {.module = "lang_vi lexicon_entry một lệnh ba vùng lúc nạp bộ lệnh", .core = CORE_NHAN};
    lang_vi_pron_t pron;
    timing_t t = {0};
    for (int h = 0; h < WARMUP_HOPS + TIMED_HOPS; h++) {
        const uint32_t start = esp_cpu_get_cycle_count();
        ESP_ERROR_CHECK(lang_vi_lexicon_entry(LANG_LINE, LANG_VI_DIALECT_ALL, &pron));
        hop_done(&t, h, start);
    }
    const unsigned stack_used = BENCH_STACK_BYTES - (unsigned)uxTaskGetStackHighWaterMark(NULL);
    report(&row, &t);
    test_report_line("alt lang_vi stack used by the bench task %u B, %u variants", stack_used,
                     pron.n_variants);
}

static void core0_benches(void *done)
{
    bench_mel();
    vTaskDelay(1);
    bench_pitch();
    vTaskDelay(1);
    bench_spk_fbank();
    xTaskNotifyGive((TaskHandle_t)done);
    vTaskDelete(NULL);
}

// Its own task, so the stack high-water mark is lang_vi's and the bench's alone.
static void lang_bench(void *done)
{
    bench_lang();
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
    run_on(lang_bench, "bench_lang", CORE_NHAN);
    test_report_line("done");
    test_report_serve();
}
