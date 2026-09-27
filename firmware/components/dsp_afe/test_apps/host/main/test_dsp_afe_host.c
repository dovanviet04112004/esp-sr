#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dsp_afe.h"
#include "dsp_afe/aec.h"
#include "dsp_afe/agc.h"
#include "dsp_afe/balance.h"
#include "dsp_afe/bss.h"
#include "dsp_afe/doa.h"
#include "dsp_afe/gsc.h"
#include "dsp_afe/hpf.h"
#include "dsp_afe/vad.h"
#include "gen_afe.h"
#include "gen_array.h"
#include "gen_grid.h"
#include "sdkconfig.h"

#if DSP_AFE_HOST_GOLDEN
#include "parity.h"
#include "test_report.h"
#endif

#define HOPS 32
#define MAX_CHANNELS 3
#define AMPLITUDE 12000.0
#define TONE_HZ 440.0
#define MAX_ERROR_LSB 1
#define CASE_BYTES_MAX (512 * 1024)
#define REPORT_LINES_MAX 1024
#define PCM_FULL_SCALE 32768.0f

#if CONFIG_DSP_AFE_VAD_ENABLE
// A real vad may call the test tone speech; its own golden cases judge it.
static const bool kVadBuilt = true;
#else
static const bool kVadBuilt = false;
#endif
#if CONFIG_DSP_AFE_AGC_ENABLE
static const bool kAgcBuilt = true;
#else
static const bool kAgcBuilt = false;
#endif

static unsigned s_failures;

static const dsp_afe_config_t kPlain = {
    .input_format = "MM",
    .spatial = DSP_AFE_SPATIAL_NONE,
    .ns_floor_db = -12.0f,
    .agc_target_dbfs = -26.0f,
    .vad_aggressiveness = 1,
};

static size_t unit_ns_bytes(void *ctx)
{
    (void)ctx;
    return sizeof(uint32_t);
}

static esp_err_t unit_ns_init(void *ctx, void *state, size_t bytes)
{
    (void)ctx;
    return state != NULL && bytes >= sizeof(uint32_t) ? ESP_OK : ESP_ERR_INVALID_ARG;
}

static esp_err_t unit_ns_process(void *ctx, void *state, const float *power, const float *echo_power,
                                 float *gain, float *speech_prob)
{
    (void)ctx;
    (void)state;
    (void)power;
    (void)echo_power;
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        gain[k] = 1.0f;
    }
    *speech_prob = 0.0f;
    return ESP_OK;
}

// A slot implementation with unit gains: the real floor learns a steady tone as noise and has its own golden
// cases.
static const dsp_afe_ns_ops_t kUnitNs = {
    .state_bytes = unit_ns_bytes,
    .init = unit_ns_init,
    .process = unit_ns_process,
};

static void check(bool ok, const char *what)
{
    printf("HOST %s: %s\n", ok ? "PASS" : "FAIL", what);
    s_failures += ok ? 0u : 1u;
}

static int16_t tone(size_t n)
{
    return (int16_t)lrint(AMPLITUDE * sin(2.0 * M_PI * TONE_HZ * (double)n / GEN_GRID_SAMPLE_RATE_HZ));
}

static uint8_t channels_of(const dsp_afe_config_t *cfg)
{
    return strcmp(cfg->input_format, "MMR") == 0 ? MAX_CHANNELS : GEN_ARRAY_N_MICS;
}

static dsp_afe_t *make(const dsp_afe_config_t *cfg)
{
    size_t hot = 0;
    size_t cold = 0;
    dsp_afe_t *afe = NULL;
    if (dsp_afe_workspace_bytes(cfg, &hot, &cold) != ESP_OK) { return NULL; }
    return dsp_afe_init(&afe, cfg, malloc(hot), hot, cold > 0 ? malloc(cold) : NULL, cold) == ESP_OK ? afe
                                                                                                     : NULL;
}

// Both microphones get the same tone, ch1 negated when opposite is set; the reference stays silent.
static void fill_hop(int16_t *interleaved, uint8_t n_channels, size_t hop, bool opposite)
{
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        const int16_t s = tone(hop * GEN_GRID_HOP_SAMPLES + i);
        interleaved[i * n_channels] = s;
        interleaved[i * n_channels + 1] = opposite ? (int16_t)-s : s;
        if (n_channels == MAX_CHANNELS) { interleaved[i * n_channels + 2] = 0; }
    }
}

// The tone of one hop, through hpf when hpf is built.
static void expected_tone(void *hpf_mem, dsp_afe_hpf_t **hpf, size_t hop, float *x)
{
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        x[i] = tone(hop * GEN_GRID_HOP_SAMPLES + i) / PCM_FULL_SCALE;
    }
#if CONFIG_DSP_AFE_HPF_ENABLE
    const dsp_afe_hpf_config_t cfg = {.cutoff_hz = GEN_AFE_HPF_CUTOFF_HZ, .n_channels = 1};
    if (*hpf == NULL) { dsp_afe_hpf_init(hpf, &cfg, hpf_mem, dsp_afe_hpf_workspace_bytes(&cfg)); }
    dsp_afe_hpf_process(*hpf, 0, x, GEN_GRID_HOP_SAMPLES);
#else
    (void)hpf_mem;
    (void)hpf;
#endif
}

#if CONFIG_DSP_AFE_AGC_ENABLE
static dsp_afe_agc_t *make_agc(const dsp_afe_config_t *cfg)
{
    const dsp_afe_agc_config_t agc_cfg = {
        .target_dbfs = cfg->agc_target_dbfs,
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
    const size_t bytes = dsp_afe_agc_workspace_bytes(&agc_cfg);
    dsp_afe_agc_t *agc = NULL;
    return dsp_afe_agc_init(&agc, &agc_cfg, malloc(bytes), bytes) == ESP_OK ? agc : NULL;
}
#endif

// The frame the chain should give for the previous hop's tone: through agc with this frame's vad when agc is
// built.
static void expected_frame(void *agc, const float *previous, uint8_t vad, int16_t *pcm, int *gain_db)
{
    static float y[GEN_GRID_HOP_SAMPLES];
    memcpy(y, previous, sizeof(y));
    float gain = 0.0f;
#if CONFIG_DSP_AFE_AGC_ENABLE
    dsp_afe_agc_process(agc, y, vad != 0, &gain);
#else
    (void)agc;
    (void)vad;
#endif
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        pcm[i] = (int16_t)lrintf(y[i] * PCM_FULL_SCALE);
    }
    *gain_db = (int)lrintf(gain);
}

static void check_round_trip(const dsp_afe_config_t *cfg, const char *what)
{
    static int16_t in[GEN_GRID_HOP_SAMPLES * MAX_CHANNELS];
    static int16_t want[GEN_GRID_HOP_SAMPLES];
    static float previous[GEN_GRID_HOP_SAMPLES];
    static uint64_t hpf_mem[64];
    dsp_afe_hpf_t *hpf = NULL;
#if CONFIG_DSP_AFE_AGC_ENABLE
    void *agc = make_agc(cfg);
#else
    void *agc = NULL;
#endif
    const uint8_t n_channels = channels_of(cfg);
    const uint16_t want_flags = n_channels == MAX_CHANNELS ? DSP_AFE_FLAG_NO_REF : 0u;
    dsp_afe_config_t unit_ns = *cfg;
    unit_ns.ns = &kUnitNs;
    dsp_afe_t *afe = make(&unit_ns);
    bool ok = afe != NULL && (agc != NULL || !kAgcBuilt);
    int worst = 0;
    memset(previous, 0, sizeof(previous));
    for (size_t h = 0; ok && h < HOPS; h++) {
        dsp_afe_frame_t out;
        int want_gain_db = 0;
        fill_hop(in, n_channels, h, false);
        ok = dsp_afe_feed(afe, in, 1) == ESP_OK && dsp_afe_fetch(afe, &out) == ESP_OK && out.seq == h &&
             out.doa_deg == -1 && (kVadBuilt || out.vad == 0) && out.flags == want_flags;
        expected_frame(agc, previous, out.vad, want, &want_gain_db);
        ok = ok && abs(out.gain_db - want_gain_db) <= 1;
        for (size_t i = 0; ok && h > 0 && i < GEN_GRID_HOP_SAMPLES; i++) {
            const int err = abs(out.pcm[i] - want[i]);
            worst = err > worst ? err : worst;
        }
        expected_tone(hpf_mem, &hpf, h, previous);
    }
    char line[160];
    snprintf(line, sizeof(line),
             "%s: the tone, through hpf and agc when built, comes back one hop late within %d LSB (worst %d)",
             what, MAX_ERROR_LSB, worst);
    check(ok && worst <= MAX_ERROR_LSB, line);
}

static void check_mix_is_the_mean(void)
{
    static int16_t in[GEN_GRID_HOP_SAMPLES * GEN_ARRAY_N_MICS];
    dsp_afe_t *afe = make(&kPlain);
    bool ok = afe != NULL;
    int worst = 0;
    for (size_t h = 0; ok && h < HOPS; h++) {
        dsp_afe_frame_t out;
        fill_hop(in, GEN_ARRAY_N_MICS, h, true);
        ok = dsp_afe_feed(afe, in, 1) == ESP_OK && dsp_afe_fetch(afe, &out) == ESP_OK;
        for (size_t i = 0; ok && i < GEN_GRID_HOP_SAMPLES; i++) {
            worst = abs(out.pcm[i]) > worst ? abs(out.pcm[i]) : worst;
        }
    }
    check(ok && worst <= MAX_ERROR_LSB, "opposite microphones cancel in the plain mix");
}

static void check_config_errors(void)
{
    size_t hot = 0;
    size_t cold = 0;
    dsp_afe_config_t cfg = kPlain;
    cfg.input_format = "XY";
    bool ok = dsp_afe_workspace_bytes(&cfg, &hot, &cold) == ESP_ERR_INVALID_ARG;
    cfg = kPlain;
    cfg.ns_floor_db = 1.0f;
    ok = ok && dsp_afe_workspace_bytes(&cfg, &hot, &cold) == ESP_ERR_INVALID_ARG;
    cfg = kPlain;
    cfg.vad_aggressiveness = 4;
    ok = ok && dsp_afe_workspace_bytes(&cfg, &hot, &cold) == ESP_ERR_INVALID_ARG;
    cfg = kPlain;
    cfg.spatial = (dsp_afe_spatial_t)7;
    ok = ok && dsp_afe_workspace_bytes(&cfg, &hot, &cold) == ESP_ERR_INVALID_ARG;
    ok = ok && dsp_afe_workspace_bytes(NULL, &hot, &cold) == ESP_ERR_INVALID_ARG;
    check(ok, "unknown formats, spatial paths and out-of-range parameters are refused");
#if !CONFIG_DSP_AFE_AEC_ENABLE && !CONFIG_DSP_AFE_GSC_ENABLE && !CONFIG_DSP_AFE_BSS_ENABLE
    cfg = kPlain;
    cfg.input_format = "MMR";
    ok = dsp_afe_workspace_bytes(&cfg, &hot, &cold) == ESP_ERR_NOT_SUPPORTED;
    cfg = kPlain;
    cfg.spatial = DSP_AFE_SPATIAL_GSC;
    ok = ok && dsp_afe_workspace_bytes(&cfg, &hot, &cold) == ESP_ERR_NOT_SUPPORTED;
    cfg.spatial = DSP_AFE_SPATIAL_BSS;
    ok = ok && dsp_afe_workspace_bytes(&cfg, &hot, &cold) == ESP_ERR_NOT_SUPPORTED;
    check(ok, "MMR, gsc and bss are refused when their modules are not built");
#endif
}

static void check_memory(void)
{
    size_t hot = 0;
    size_t cold = 0;
    dsp_afe_t *afe = NULL;
    const bool sized = dsp_afe_workspace_bytes(&kPlain, &hot, &cold) == ESP_OK;
    printf("HOST INFO plain chain: hot %zu B, cold %zu B\n", hot, cold);
    void *mem = malloc(hot);
    check(sized && cold == 0 && dsp_afe_init(&afe, &kPlain, mem, hot - 1, NULL, 0) == ESP_ERR_INVALID_SIZE,
          "a hot region one byte short is refused, and nothing needs cold yet");
    free(mem);
}

static void check_fifo(void)
{
    static int16_t in[DSP_AFE_FIFO_FRAMES * GEN_GRID_HOP_SAMPLES * GEN_ARRAY_N_MICS];
    dsp_afe_t *afe = make(&kPlain);
    dsp_afe_frame_t out;
    dsp_afe_stats_t stats;
    bool ok = afe != NULL && dsp_afe_fetch(afe, &out) == ESP_ERR_NOT_FOUND &&
              dsp_afe_feed(afe, in, DSP_AFE_FIFO_FRAMES) == ESP_OK &&
              dsp_afe_feed(afe, in, 1) == ESP_ERR_NO_MEM;
    for (size_t f = 0; ok && f < DSP_AFE_FIFO_FRAMES; f++) {
        ok = dsp_afe_fetch(afe, &out) == ESP_OK && out.seq == f;
    }
    if (ok) { dsp_afe_stats(afe, &stats); }
    check(ok && dsp_afe_fetch(afe, &out) == ESP_ERR_NOT_FOUND && stats.hops_in == DSP_AFE_FIFO_FRAMES &&
              stats.hops_out == DSP_AFE_FIFO_FRAMES && stats.fifo_overflows == 1,
          "the inner fifo holds DSP_AFE_FIFO_FRAMES hops and refuses more without consuming any");
}

static void check_gap_and_clip(void)
{
    static int16_t in[GEN_GRID_HOP_SAMPLES * GEN_ARRAY_N_MICS];
    dsp_afe_t *afe = make(&kPlain);
    dsp_afe_frame_t first;
    dsp_afe_frame_t after_gap;
    dsp_afe_frame_t clipped;
    dsp_afe_stats_t stats;
    bool ok = afe != NULL && dsp_afe_feed(afe, in, 1) == ESP_OK && dsp_afe_fetch(afe, &first) == ESP_OK;
    if (ok) { dsp_afe_reset(afe); }
    in[0] = INT16_MAX;
    ok = ok && dsp_afe_feed(afe, in, 1) == ESP_OK && dsp_afe_fetch(afe, &after_gap) == ESP_OK;
    in[0] = 0;
    in[1] = INT16_MIN;
    ok = ok && dsp_afe_feed(afe, in, 1) == ESP_OK && dsp_afe_fetch(afe, &clipped) == ESP_OK;
    if (ok) { dsp_afe_stats(afe, &stats); }
    check(ok && first.flags == 0 && after_gap.flags == (DSP_AFE_FLAG_GAP | DSP_AFE_FLAG_CLIPPED) &&
              after_gap.seq == 1 && clipped.flags == DSP_AFE_FLAG_CLIPPED && stats.gaps == 1 &&
              stats.clipped_hops == 2,
          "reset marks the next hop only, seq runs on, full-scale samples on either microphone flag a clip");
}

static void check_params(void)
{
    dsp_afe_t *afe = make(&kPlain);
    const bool ok = afe != NULL &&
                    dsp_afe_set_param(afe, DSP_AFE_PARAM_NS_FLOOR_DB, 1.0f) == ESP_ERR_INVALID_ARG &&
                    dsp_afe_set_param(afe, DSP_AFE_PARAM_NS_FLOOR_DB, -18.0f) == ESP_OK &&
                    dsp_afe_set_param(afe, DSP_AFE_PARAM_VAD_AGGRESSIVENESS, 2.5f) == ESP_ERR_INVALID_ARG &&
                    dsp_afe_set_param(afe, DSP_AFE_PARAM_VAD_AGGRESSIVENESS, 3.0f) == ESP_OK &&
                    dsp_afe_set_param(afe, DSP_AFE_PARAM_AGC_TARGET_DBFS, 2.0f) == ESP_ERR_INVALID_ARG &&
                    dsp_afe_set_param(afe, DSP_AFE_PARAM_AGC_TARGET_DBFS, -20.0f) == ESP_OK &&
                    dsp_afe_set_param(afe, (dsp_afe_param_t)9, 0.0f) == ESP_ERR_INVALID_ARG;
    check(ok, "set_param takes values in range and refuses the rest");
}

#if DSP_AFE_HOST_ALL_MODULES
static void *region(size_t bytes)
{
    return bytes > 0 ? malloc(bytes) : NULL;
}

static void check_module_shells(void)
{
    static float hop[GEN_GRID_HOP_SAMPLES];
    static dsp_spec_cplx_t x0[GEN_GRID_N_BINS], x1[GEN_GRID_N_BINS], y0[GEN_GRID_N_BINS], y1[GEN_GRID_N_BINS];
    static float power[GEN_GRID_N_BINS], gain[GEN_GRID_N_BINS];
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        x0[k] = (dsp_spec_cplx_t){1.0f, 2.0f};
        x1[k] = (dsp_spec_cplx_t){3.0f, -2.0f};
    }
    const dsp_afe_hpf_config_t hpf_cfg = {.cutoff_hz = 80.0f, .n_channels = 2};
    const dsp_afe_hpf_config_t hpf_bad = {.cutoff_hz = 5.0f, .n_channels = 2};
    dsp_afe_hpf_t *hpf = NULL;
    size_t bytes = dsp_afe_hpf_workspace_bytes(&hpf_cfg);
    check(dsp_afe_hpf_workspace_bytes(&hpf_bad) == 0 &&
              dsp_afe_hpf_init(&hpf, &hpf_cfg, region(bytes), bytes) == ESP_OK &&
              dsp_afe_hpf_process(hpf, 0, hop, GEN_GRID_HOP_SAMPLES) == ESP_OK &&
              dsp_afe_hpf_process(hpf, 2, hop, GEN_GRID_HOP_SAMPLES) == ESP_ERR_INVALID_ARG,
          "hpf: refuses a 5 Hz cutoff and a third channel");

    const size_t fft_bytes = dsp_spec_fft_workspace_bytes(GEN_GRID_FFT_SIZE);
    dsp_spec_fft_t *fft = NULL;
    const dsp_afe_aec_config_t aec_cfg = {.n_mics = 2, .n_partitions = 8};
    dsp_afe_aec_t *aec = NULL;
    dsp_afe_aec_stats_t aec_stats;
    float *mics[2] = {hop, hop};
    power[0] = 5.0f;
    bytes = dsp_afe_aec_workspace_bytes(&aec_cfg);
    const bool aec_ok = dsp_spec_fft_init(&fft, GEN_GRID_FFT_SIZE, malloc(fft_bytes), fft_bytes) == ESP_OK &&
                        dsp_afe_aec_init(&aec, &aec_cfg, NULL, region(bytes), bytes) == ESP_ERR_INVALID_ARG &&
                        dsp_afe_aec_init(&aec, &aec_cfg, fft, region(bytes), bytes) == ESP_OK &&
                        dsp_afe_aec_process(aec, mics, hop, power) == ESP_OK && power[0] == 0.0f;
    if (aec_ok) { dsp_afe_aec_stats(aec, &aec_stats); }
    check(aec_ok && !aec_stats.diverged, "aec shell: needs the shared fft, reports no residual echo");

    memcpy(y1, x1, sizeof(y1));
    dsp_afe_balance_apply(x0, y1);
    bool balance_ok = true;
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        balance_ok = balance_ok && y1[k].re == 7.0f && y1[k].im == 4.0f;
    }
    check(balance_ok, "balance: (1+2j)(3-2j) = 7+4j on every bin");

    const dsp_afe_doa_config_t doa_cfg = {
        GEN_ARRAY_SPACING_M, GEN_ARRAY_SPEED_OF_SOUND_M_S, 200.0f, GEN_ARRAY_ALIAS_HZ, 2.0f, 0.2f};
    dsp_afe_doa_t *doa = NULL;
    dsp_afe_doa_result_t doa_out = {.angle_deg = 45, .confidence = 9};
    bytes = dsp_afe_doa_workspace_bytes(&doa_cfg);
    check(dsp_afe_doa_init(&doa, &doa_cfg, region(bytes), bytes) == ESP_OK &&
              dsp_afe_doa_process(doa, x0, x1, true, &doa_out) == ESP_OK && doa_out.angle_deg == -1 &&
              doa_out.confidence == 0,
          "doa shell: the angle stays unknown");

    const dsp_afe_gsc_config_t gsc_cfg = {GEN_ARRAY_SPACING_M, GEN_ARRAY_SPEED_OF_SOUND_M_S, 0.05f, 1e-4f,
                                          0.0f};
    dsp_afe_gsc_t *gsc = NULL;
    bytes = dsp_afe_gsc_workspace_bytes(&gsc_cfg);
    check(dsp_afe_gsc_init(&gsc, &gsc_cfg, region(bytes), bytes) == ESP_OK &&
              dsp_afe_gsc_process(gsc, x0, x1, 30.0f, true, y0) == ESP_OK && y0[7].re == 2.0f &&
              y0[7].im == 0.0f,
          "gsc shell: writes the mean of both microphones");

    const dsp_afe_bss_config_t bss_cfg = {1.0f, GEN_ARRAY_SPACING_M, GEN_ARRAY_SPEED_OF_SOUND_M_S};
    dsp_afe_bss_t *bss = NULL;
    int16_t angles[2] = {0, 0};
    bytes = dsp_afe_bss_workspace_bytes(&bss_cfg);
    const bool bss_ok = dsp_afe_bss_init(&bss, &bss_cfg, region(bytes), bytes) == ESP_OK &&
                        dsp_afe_bss_process(bss, x0, x1, y0, y1) == ESP_OK &&
                        memcmp(y0, x0, sizeof(y0)) == 0 && memcmp(y1, x1, sizeof(y1)) == 0;
    if (bss_ok) { dsp_afe_bss_directions(bss, angles); }
    check(bss_ok && angles[0] == -1 && angles[1] == -1,
          "bss shell: copies each microphone, directions unknown");

    dsp_afe_ns_omlsa_config_t ns_cfg = {.floor_db = -12.0f};
    const dsp_afe_ns_ops_t *ns = dsp_afe_ns_omlsa_ops();
    float speech_prob = 1.0f;
    bytes = ns->state_bytes(&ns_cfg);
    void *ns_state = region(bytes);
    const bool ns_ok = ns->init(&ns_cfg, ns_state, bytes) == ESP_OK &&
                       ns->process(&ns_cfg, ns_state, power, NULL, gain, &speech_prob) == ESP_OK &&
                       gain[0] == 1.0f && gain[GEN_GRID_N_BINS - 1] == 1.0f && speech_prob == 0.0f;
    ns_cfg.floor_db = 3.0f;
    check(ns_ok && ns->state_bytes(&ns_cfg) == 0,
          "ns_omlsa: unit gains before any power, refuses a positive floor");

    const dsp_afe_vad_config_t vad_cfg = {.aggressiveness = 3, .hangover_ms = 240};
    const dsp_afe_vad_config_t vad_bad = {.aggressiveness = 4, .hangover_ms = 240};
    dsp_afe_vad_t *vad = NULL;
    bool speech = true;
    bytes = dsp_afe_vad_workspace_bytes(&vad_cfg);
    check(dsp_afe_vad_workspace_bytes(&vad_bad) == 0 &&
              dsp_afe_vad_init(&vad, &vad_cfg, region(bytes), bytes) == ESP_OK &&
              dsp_afe_vad_process(vad, hop, &speech) == ESP_OK && !speech,
          "vad: a silent hop is not speech, aggressiveness 4 refused");

    dsp_afe_agc_config_t agc_cfg = {
        .target_dbfs = GEN_AFE_AGC_TARGET_DBFS,
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
    dsp_afe_agc_t *agc = NULL;
    float gain_db = 5.0f;
    bytes = dsp_afe_agc_workspace_bytes(&agc_cfg);
    const bool agc_ok = dsp_afe_agc_init(&agc, &agc_cfg, region(bytes), bytes) == ESP_OK &&
                        dsp_afe_agc_process(agc, hop, false, &gain_db) == ESP_OK && gain_db == 0.0f;
    if (agc_ok) { dsp_afe_agc_set_target(agc, -20.0f); }
    agc_cfg.release_ms = 0.0f;
    check(agc_ok && dsp_afe_agc_workspace_bytes(&agc_cfg) == 0,
          "agc: 0 dB without speech, refuses a release of 0 ms");
}

static void check_every_path(void)
{
    dsp_afe_config_t cfg = kPlain;
    cfg.spatial = DSP_AFE_SPATIAL_GSC;
    check_round_trip(&cfg, "gsc path through every shell");
    cfg.spatial = DSP_AFE_SPATIAL_BSS;
    check_round_trip(&cfg, "bss path through every shell");
    cfg.input_format = "MMR";
    check_round_trip(&cfg, "MMR with aec and bss through every shell, silent reference flagged");
}

static void check_balance_in_chain(void)
{
    static dsp_afe_calib_t calib;
    static int16_t in[GEN_GRID_HOP_SAMPLES * GEN_ARRAY_N_MICS];
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        calib.balance[k] = (dsp_spec_cplx_t){1.0f, 0.0f};
    }
    dsp_afe_config_t cfg = kPlain;
    cfg.calib = &calib;
    check_round_trip(&cfg, "unit balance gains");
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        calib.balance[k] = (dsp_spec_cplx_t){-1.0f, 0.0f};
    }
    dsp_afe_t *afe = make(&cfg);
    bool ok = afe != NULL;
    int worst = 0;
    for (size_t h = 0; ok && h < HOPS; h++) {
        dsp_afe_frame_t out;
        fill_hop(in, GEN_ARRAY_N_MICS, h, false);
        ok = dsp_afe_feed(afe, in, 1) == ESP_OK && dsp_afe_fetch(afe, &out) == ESP_OK;
        for (size_t i = 0; ok && i < GEN_GRID_HOP_SAMPLES; i++) {
            worst = abs(out.pcm[i]) > worst ? abs(out.pcm[i]) : worst;
        }
    }
    check(ok && worst == 0, "balance gains of -1 on ch1 cancel identical microphones in the plain mix");
}
#endif

#if CONFIG_DSP_AFE_NS_OMLSA_ENABLE
static float mean_level_dbfs(const dsp_afe_config_t *cfg, size_t hops, size_t from)
{
    static int16_t in[GEN_GRID_HOP_SAMPLES * GEN_ARRAY_N_MICS];
    uint32_t lcg = 12345u;
    dsp_afe_t *afe = make(cfg);
    float sum = 0.0f;
    for (size_t h = 0; afe != NULL && h < hops; h++) {
        for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES * GEN_ARRAY_N_MICS; i++) {
            lcg = lcg * 1664525u + 1013904223u;
            in[i] = (int16_t)((int32_t)(lcg >> 16) - 32768) / 16;
        }
        dsp_afe_frame_t out;
        if (dsp_afe_feed(afe, in, 1) != ESP_OK || dsp_afe_fetch(afe, &out) != ESP_OK) { return 0.0f; }
        sum += h >= from ? out.level_dbfs : 0;
    }
    return afe != NULL ? sum / (float)(hops - from) : 0.0f;
}

static void check_ns_floor_in_chain(void)
{
    dsp_afe_config_t unit_ns = kPlain;
    unit_ns.ns = &kUnitNs;
    const float floor_db = mean_level_dbfs(&kPlain, HOPS * 8, HOPS * 5);
    const float unit_db = mean_level_dbfs(&unit_ns, HOPS * 8, HOPS * 5);
    char line[160];
    snprintf(line, sizeof(line),
             "ns_omlsa by default: steady noise %.1f dB under the unit-gain slot (floor %.0f dB)",
             (double)(unit_db - floor_db), (double)kPlain.ns_floor_db);
    check(unit_db - floor_db > -kPlain.ns_floor_db - 3.0f, line);
}
#endif

#if DSP_AFE_HOST_GOLDEN
static bool read_case(const char *path, void *buf, size_t cap, size_t *len)
{
    FILE *file = fopen(path, "rb");
    if (file == NULL) { return false; }
    *len = fread(buf, 1, cap, file);
    const bool whole = feof(file) != 0;
    fclose(file);
    return whole;
}

static void run_golden(const char *root)
{
    static uint8_t buf[CASE_BYTES_MAX];
    test_report_begin("PARITY", REPORT_LINES_MAX);
#if DSP_AFE_HOST_ALL_MODULES
    const unsigned cases =
        parity_run_block(root, "hpf", parity_hpf, read_case, buf, sizeof(buf), &s_failures) +
        parity_run_block(root, "balance", parity_balance, read_case, buf, sizeof(buf), &s_failures) +
        parity_run_block(root, "ns_omlsa", parity_ns_omlsa, read_case, buf, sizeof(buf), &s_failures) +
        parity_run_block(root, "vad", parity_vad, read_case, buf, sizeof(buf), &s_failures) +
        parity_run_block(root, "agc", parity_agc, read_case, buf, sizeof(buf), &s_failures);
#elif DSP_AFE_HOST_PRODUCT
    const unsigned cases = parity_run_block(root, "chain_modules", parity_chain_modules, read_case, buf,
                                            sizeof(buf), &s_failures);
#else
    const unsigned cases =
        parity_run_block(root, "chain", parity_chain, read_case, buf, sizeof(buf), &s_failures);
#endif
    test_report_line("done %u cases", cases);
    test_report_serve();
}
#endif

int main(int argc, char **argv)
{
    check_round_trip(&kPlain, "plain chain");
    check_mix_is_the_mean();
    check_config_errors();
    check_memory();
    check_fifo();
    check_gap_and_clip();
    check_params();
#if DSP_AFE_HOST_ALL_MODULES
    check_module_shells();
    check_every_path();
    check_balance_in_chain();
#endif
#if CONFIG_DSP_AFE_NS_OMLSA_ENABLE
    check_ns_floor_in_chain();
#endif
#if DSP_AFE_HOST_GOLDEN
    if (argc > 1) { run_golden(argv[1]); }
#else
    (void)argc;
    (void)argv;
#endif
    printf("HOST %u failure(s)\n", s_failures);
    return s_failures == 0 ? 0 : 1;
}
