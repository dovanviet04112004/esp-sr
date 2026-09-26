#include "dsp_afe.h"

#include <math.h>
#include <string.h>

#include "afe_internal.h"
#include "dsp_afe/aec.h"
#include "dsp_afe/agc.h"
#include "dsp_afe/balance.h"
#include "dsp_afe/bss.h"
#include "dsp_afe/doa.h"
#include "dsp_afe/gsc.h"
#include "dsp_afe/hpf.h"
#include "dsp_afe/vad.h"
#include "dsp_spec/stft.h"
#include "gen_afe.h"
#include "gen_array.h"
#include "sdkconfig.h"

#define N_MICS GEN_ARRAY_N_MICS
#define MAX_CHANNELS (N_MICS + 1) // "MMR" appends the loudspeaker reference
#define PCM_FULL_SCALE 32768.0f
#define BROADSIDE_DEG ((GEN_ARRAY_DOA_MIN_DEG + GEN_ARRAY_DOA_MAX_DEG) / 2.0f)
#define ANGLE_UNKNOWN_DEG (-1)
#define VAD_AGGRESSIVENESS_MAX 3 // dsp_afe_vad_config_t: 0 most lenient .. 3 strictest

_Static_assert(N_MICS == 2, "the spatial stage takes exactly two microphones");

typedef enum {
    WALK_SIZE,  // count bytes only
    WALK_BUILD, // carve and initialise every region
    WALK_RESET, // same carving, fresh adaptive state, fft tables kept
} walk_mode_t;

typedef struct {
    afe_arena_t *arena;
    walk_mode_t mode;
    esp_err_t err;
} walker_t;

struct dsp_afe_s {
    dsp_afe_config_t cfg; // calib points at the copy below
    uint8_t n_channels;
    void *hot;
    size_t hot_bytes;
    dsp_afe_calib_t calib;
    dsp_spec_fft_t *fft;
    dsp_spec_stft_t *analysis[N_MICS];
    dsp_spec_istft_t *synthesis;
    const dsp_afe_ns_ops_t *ns;
    void *ns_ctx;
    void *ns_state;
    dsp_afe_ns_omlsa_config_t omlsa;
#if CONFIG_DSP_AFE_HPF_ENABLE
    dsp_afe_hpf_t *hpf;
#endif
#if CONFIG_DSP_AFE_AEC_ENABLE
    dsp_afe_aec_t *aec;
#endif
#if CONFIG_DSP_AFE_DOA_ENABLE
    dsp_afe_doa_t *doa;
#endif
#if CONFIG_DSP_AFE_GSC_ENABLE
    dsp_afe_gsc_t *gsc;
#endif
#if CONFIG_DSP_AFE_BSS_ENABLE
    dsp_afe_bss_t *bss;
#endif
#if CONFIG_DSP_AFE_VAD_ENABLE
    dsp_afe_vad_t *vad;
    void *vad_mem;
    size_t vad_bytes;
#endif
#if CONFIG_DSP_AFE_AGC_ENABLE
    dsp_afe_agc_t *agc;
#endif
    float pcm[MAX_CHANNELS][GEN_GRID_HOP_SAMPLES];
    dsp_spec_cplx_t bins[N_MICS][GEN_GRID_N_BINS];
    dsp_spec_cplx_t beam[N_MICS][GEN_GRID_N_BINS];
    float power[GEN_GRID_N_BINS];
    float echo_power[GEN_GRID_N_BINS];
    float gain[GEN_GRID_N_BINS];
    float clean[GEN_GRID_HOP_SAMPLES];
    dsp_afe_frame_t fifo[DSP_AFE_FIFO_FRAMES];
    uint8_t fifo_head;
    uint8_t fifo_count;
    uint32_t seq;
    bool gap_pending;
    bool last_vad;
    dsp_afe_doa_result_t doa_result;
    dsp_afe_stats_t stats;
};

static bool param_ok(dsp_afe_param_t param, float value)
{
    switch (param) {
    case DSP_AFE_PARAM_NS_FLOOR_DB:
    case DSP_AFE_PARAM_AGC_TARGET_DBFS: return value <= 0.0f;
    case DSP_AFE_PARAM_VAD_AGGRESSIVENESS:
        return value >= 0.0f && value <= VAD_AGGRESSIVENESS_MAX && value == floorf(value);
    default: return false;
    }
}

static esp_err_t check_config(const dsp_afe_config_t *cfg, uint8_t *n_channels)
{
    if (cfg == NULL || cfg->input_format == NULL) { return ESP_ERR_INVALID_ARG; }
    if (strcmp(cfg->input_format, "MM") == 0) {
        *n_channels = N_MICS;
    } else if (strcmp(cfg->input_format, "MMR") == 0) {
        *n_channels = N_MICS + 1;
    } else {
        return ESP_ERR_INVALID_ARG;
    }
    const bool params_ok = param_ok(DSP_AFE_PARAM_NS_FLOOR_DB, cfg->ns_floor_db) &&
                           param_ok(DSP_AFE_PARAM_AGC_TARGET_DBFS, cfg->agc_target_dbfs) &&
                           param_ok(DSP_AFE_PARAM_VAD_AGGRESSIVENESS, cfg->vad_aggressiveness);
    return params_ok ? ESP_OK : ESP_ERR_INVALID_ARG;
}

static void fail(walker_t *w, esp_err_t err)
{
    if (w->err == ESP_OK) { w->err = err; }
}

// A zero size means the module refused its configuration; the block is NULL while sizing.
static void *region(walker_t *w, size_t bytes)
{
    if (bytes == 0) { fail(w, ESP_ERR_INVALID_ARG); }
    if (w->err != ESP_OK) { return NULL; }
    void *mem = afe_take(w->arena, bytes);
    if (w->mode != WALK_SIZE && mem == NULL) { fail(w, ESP_ERR_INVALID_SIZE); }
    return w->mode == WALK_SIZE ? NULL : mem;
}

static void walk_spec(walker_t *w, dsp_afe_t *afe)
{
    const size_t fft_bytes = dsp_spec_fft_workspace_bytes(GEN_GRID_FFT_SIZE);
    void *mem = region(w, fft_bytes);
    if (mem != NULL && w->mode == WALK_BUILD) {
        fail(w, dsp_spec_fft_init(&afe->fft, GEN_GRID_FFT_SIZE, mem, fft_bytes));
    }
    for (size_t m = 0; m < N_MICS; m++) {
        mem = region(w, dsp_spec_stft_workspace_bytes());
        if (mem != NULL) {
            fail(w, dsp_spec_stft_init(&afe->analysis[m], afe->fft, mem, dsp_spec_stft_workspace_bytes()));
        }
    }
    mem = region(w, dsp_spec_istft_workspace_bytes());
    if (mem != NULL) {
        fail(w, dsp_spec_istft_init(&afe->synthesis, afe->fft, mem, dsp_spec_istft_workspace_bytes()));
    }
}

static void walk_hpf(walker_t *w, dsp_afe_t *afe)
{
#if CONFIG_DSP_AFE_HPF_ENABLE
    const dsp_afe_hpf_config_t cfg = {.cutoff_hz = GEN_AFE_HPF_CUTOFF_HZ, .n_channels = N_MICS};
    const size_t bytes = dsp_afe_hpf_workspace_bytes(&cfg);
    void *mem = region(w, bytes);
    if (mem != NULL) { fail(w, dsp_afe_hpf_init(&afe->hpf, &cfg, mem, bytes)); }
#else
    (void)w;
    (void)afe;
#endif
}

static void walk_aec(walker_t *w, const dsp_afe_config_t *cfg, uint8_t n_channels, dsp_afe_t *afe)
{
    if (n_channels <= N_MICS) { return; }
#if CONFIG_DSP_AFE_AEC_ENABLE
    const dsp_afe_aec_config_t aec = {
        .n_mics = N_MICS,
        .n_partitions = GEN_AFE_AEC_PARTITIONS,
        .bulk_delay_samples = cfg->calib != NULL ? cfg->calib->aec_delay_samples : 0,
    };
    const size_t bytes = dsp_afe_aec_workspace_bytes(&aec);
    void *mem = region(w, bytes);
    if (mem != NULL) { fail(w, dsp_afe_aec_init(&afe->aec, &aec, afe->fft, mem, bytes)); }
#else
    (void)cfg;
    (void)afe;
    fail(w, ESP_ERR_NOT_SUPPORTED);
#endif
}

static void walk_doa(walker_t *w, dsp_afe_t *afe)
{
#if CONFIG_DSP_AFE_DOA_ENABLE
    const dsp_afe_doa_config_t cfg = {
        .spacing_m = GEN_ARRAY_SPACING_M,
        .speed_of_sound_m_s = GEN_ARRAY_SPEED_OF_SOUND_M_S,
        .band_min_hz = GEN_AFE_DOA_BAND_MIN_HZ,
        .band_max_hz = GEN_AFE_DOA_BAND_MAX_HZ > 0.0f ? GEN_AFE_DOA_BAND_MAX_HZ : GEN_ARRAY_ALIAS_HZ,
        .grid_step_deg = GEN_AFE_DOA_GRID_STEP_DEG,
        .smooth_tau_s = GEN_AFE_DOA_SMOOTH_TAU_S,
    };
    const size_t bytes = dsp_afe_doa_workspace_bytes(&cfg);
    void *mem = region(w, bytes);
    if (mem != NULL) { fail(w, dsp_afe_doa_init(&afe->doa, &cfg, mem, bytes)); }
#else
    (void)w;
    (void)afe;
#endif
}

static void walk_spatial(walker_t *w, dsp_afe_spatial_t spatial, dsp_afe_t *afe)
{
    switch (spatial) {
    case DSP_AFE_SPATIAL_NONE: return;
    case DSP_AFE_SPATIAL_GSC: {
#if CONFIG_DSP_AFE_GSC_ENABLE
        const dsp_afe_gsc_config_t cfg = {
            .spacing_m = GEN_ARRAY_SPACING_M,
            .speed_of_sound_m_s = GEN_ARRAY_SPEED_OF_SOUND_M_S,
            .step_size = GEN_AFE_GSC_STEP_SIZE,
            .leakage = GEN_AFE_GSC_LEAKAGE,
        };
        const size_t bytes = dsp_afe_gsc_workspace_bytes(&cfg);
        void *mem = region(w, bytes);
        if (mem != NULL) { fail(w, dsp_afe_gsc_init(&afe->gsc, &cfg, mem, bytes)); }
#else
        fail(w, ESP_ERR_NOT_SUPPORTED);
#endif
        return;
    }
    case DSP_AFE_SPATIAL_BSS: {
#if CONFIG_DSP_AFE_BSS_ENABLE
        const dsp_afe_bss_config_t cfg = {
            .forget_tau_s = GEN_AFE_BSS_FORGET_TAU_S,
            .spacing_m = GEN_ARRAY_SPACING_M,
            .speed_of_sound_m_s = GEN_ARRAY_SPEED_OF_SOUND_M_S,
        };
        const size_t bytes = dsp_afe_bss_workspace_bytes(&cfg);
        void *mem = region(w, bytes);
        if (mem != NULL) { fail(w, dsp_afe_bss_init(&afe->bss, &cfg, mem, bytes)); }
#else
        fail(w, ESP_ERR_NOT_SUPPORTED);
#endif
        return;
    }
    default:
        (void)afe;
        fail(w, ESP_ERR_INVALID_ARG);
        return;
    }
}

static void walk_ns(walker_t *w, const dsp_afe_config_t *cfg, dsp_afe_t *afe)
{
    const dsp_afe_ns_ops_t *ops = cfg->ns;
    void *ctx = cfg->ns_ctx;
#if CONFIG_DSP_AFE_NS_OMLSA_ENABLE
    dsp_afe_ns_omlsa_config_t sizing_ctx = {.floor_db = cfg->ns_floor_db};
    if (ops == NULL) {
        ops = dsp_afe_ns_omlsa_ops();
        ctx = &sizing_ctx;
        if (afe != NULL) {
            afe->omlsa = sizing_ctx;
            ctx = &afe->omlsa;
        }
    }
#endif
    if (ops == NULL) { return; }
    const size_t bytes = ops->state_bytes(ctx);
    void *mem = region(w, bytes);
    if (mem != NULL) {
        afe->ns = ops;
        afe->ns_ctx = ctx;
        afe->ns_state = mem;
        fail(w, ops->init(ctx, mem, bytes));
    }
}

#if CONFIG_DSP_AFE_VAD_ENABLE
static dsp_afe_vad_config_t vad_config(const dsp_afe_config_t *cfg)
{
    return (dsp_afe_vad_config_t){.aggressiveness = cfg->vad_aggressiveness,
                                  .hangover_ms = GEN_AFE_VAD_HANGOVER_MS};
}
#endif

static void walk_vad(walker_t *w, const dsp_afe_config_t *cfg, dsp_afe_t *afe)
{
#if CONFIG_DSP_AFE_VAD_ENABLE
    const dsp_afe_vad_config_t vad = vad_config(cfg);
    const size_t bytes = dsp_afe_vad_workspace_bytes(&vad);
    void *mem = region(w, bytes);
    if (mem != NULL) {
        afe->vad_mem = mem;
        afe->vad_bytes = bytes;
        fail(w, dsp_afe_vad_init(&afe->vad, &vad, mem, bytes));
    }
#else
    (void)w;
    (void)cfg;
    (void)afe;
#endif
}

static void walk_agc(walker_t *w, const dsp_afe_config_t *cfg, dsp_afe_t *afe)
{
#if CONFIG_DSP_AFE_AGC_ENABLE
    const dsp_afe_agc_config_t agc = {
        .target_dbfs = cfg->agc_target_dbfs,
        .up_db_per_s = GEN_AFE_AGC_UP_DB_PER_S,
        .down_db_per_s = GEN_AFE_AGC_DOWN_DB_PER_S,
        .limit_dbfs = GEN_AFE_AGC_LIMIT_DBFS,
        .lookahead_ms = GEN_AFE_AGC_LOOKAHEAD_MS,
    };
    const size_t bytes = dsp_afe_agc_workspace_bytes(&agc);
    void *mem = region(w, bytes);
    if (mem != NULL) { fail(w, dsp_afe_agc_init(&afe->agc, &agc, mem, bytes)); }
#else
    (void)w;
    (void)cfg;
    (void)afe;
#endif
}

// One walk sizes, builds and resets, so the three can never disagree on the layout of hot.
static esp_err_t walk(const dsp_afe_config_t *cfg, uint8_t n_channels, dsp_afe_t *afe, afe_arena_t *arena,
                      walk_mode_t mode)
{
    walker_t w = {.arena = arena, .mode = mode, .err = ESP_OK};
    walk_spec(&w, afe);
    walk_hpf(&w, afe);
    walk_aec(&w, cfg, n_channels, afe);
    walk_doa(&w, afe);
    walk_spatial(&w, cfg->spatial, afe);
    walk_ns(&w, cfg, afe);
    walk_vad(&w, cfg, afe);
    walk_agc(&w, cfg, afe);
    return w.err;
}

esp_err_t dsp_afe_workspace_bytes(const dsp_afe_config_t *cfg, size_t *hot_bytes, size_t *cold_bytes)
{
    uint8_t n_channels = 0;
    if (hot_bytes == NULL || cold_bytes == NULL) { return ESP_ERR_INVALID_ARG; }
    esp_err_t err = check_config(cfg, &n_channels);
    if (err != ESP_OK) { return err; }
    afe_arena_t sizing = afe_arena(NULL, 0);
    afe_take(&sizing, sizeof(struct dsp_afe_s));
    err = walk(cfg, n_channels, NULL, &sizing, WALK_SIZE);
    if (err != ESP_OK) { return err; }
    *hot_bytes = AFE_ALIGN_BYTES + sizing.used;
    *cold_bytes = 0;
    return ESP_OK;
}

esp_err_t dsp_afe_init(dsp_afe_t **out, const dsp_afe_config_t *cfg, void *hot, size_t hot_bytes, void *cold,
                       size_t cold_bytes)
{
    size_t need_hot = 0;
    size_t need_cold = 0;
    if (out == NULL || hot == NULL) { return ESP_ERR_INVALID_ARG; }
    esp_err_t err = dsp_afe_workspace_bytes(cfg, &need_hot, &need_cold);
    if (err != ESP_OK) { return err; }
    if (need_cold > 0 && cold == NULL) { return ESP_ERR_INVALID_ARG; }
    if (hot_bytes < need_hot || cold_bytes < need_cold) { return ESP_ERR_INVALID_SIZE; }
    afe_arena_t arena = afe_arena(hot, hot_bytes);
    dsp_afe_t *afe = afe_take(&arena, sizeof(*afe));
    memset(afe, 0, sizeof(*afe));
    afe->cfg = *cfg;
    check_config(cfg, &afe->n_channels);
    afe->hot = hot;
    afe->hot_bytes = hot_bytes;
    if (cfg->calib != NULL) {
        afe->calib = *cfg->calib;
        afe->cfg.calib = &afe->calib;
    }
    afe->doa_result.angle_deg = ANGLE_UNKNOWN_DEG;
    err = walk(&afe->cfg, afe->n_channels, afe, &arena, WALK_BUILD);
    if (err == ESP_OK) { *out = afe; }
    return err;
}

static void to_float(dsp_afe_t *afe, const int16_t *interleaved, bool *clipped, bool *ref_silent)
{
    const uint8_t n = afe->n_channels;
    bool clip = false;
    bool silent = true;
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        for (uint8_t ch = 0; ch < n; ch++) {
            const int16_t s = interleaved[i * n + ch];
            clip = clip || (ch < N_MICS && (s == INT16_MAX || s == INT16_MIN));
            silent = silent && (ch < N_MICS || s == 0);
            afe->pcm[ch][i] = (float)s / PCM_FULL_SCALE;
        }
    }
    *clipped = clip;
    *ref_silent = n > N_MICS && silent;
}

static void run_echo_stage(dsp_afe_t *afe, uint16_t *flags)
{
#if CONFIG_DSP_AFE_HPF_ENABLE
    for (uint8_t m = 0; m < N_MICS; m++) {
        dsp_afe_hpf_process(afe->hpf, m, afe->pcm[m], GEN_GRID_HOP_SAMPLES);
    }
#endif
#if CONFIG_DSP_AFE_AEC_ENABLE
    if (afe->n_channels > N_MICS) {
        float *const mics[N_MICS] = {afe->pcm[0], afe->pcm[1]};
        dsp_afe_aec_process(afe->aec, mics, afe->pcm[N_MICS], afe->echo_power);
        dsp_afe_aec_stats_t aec;
        dsp_afe_aec_stats(afe->aec, &aec);
        *flags |= aec.diverged ? DSP_AFE_FLAG_AEC_DIVERGED : 0u;
    }
#else
    (void)afe;
    (void)flags;
#endif
}

static dsp_spec_cplx_t *run_spatial_stage(dsp_afe_t *afe)
{
    const dsp_spec_cplx_t *x0 = afe->bins[0];
    const dsp_spec_cplx_t *x1 = afe->bins[1];
    dsp_spec_cplx_t *y = afe->beam[0];
#if CONFIG_DSP_AFE_BALANCE_ENABLE
    if (afe->cfg.calib != NULL) { dsp_afe_balance_apply(afe->calib.balance, afe->bins[1]); }
#endif
#if CONFIG_DSP_AFE_DOA_ENABLE
    const bool update = afe->last_vad && afe->seq % GEN_AFE_DOA_UPDATE_EVERY_HOPS == 0;
    dsp_afe_doa_process(afe->doa, x0, x1, update, &afe->doa_result);
#endif
    switch (afe->cfg.spatial) {
#if CONFIG_DSP_AFE_GSC_ENABLE
    case DSP_AFE_SPATIAL_GSC: {
        const float angle_deg = afe->doa_result.angle_deg >= 0 ? afe->doa_result.angle_deg : BROADSIDE_DEG;
        // Adapt only without a talker, or the canceller learns to remove them (KEHOACH 3.7).
        dsp_afe_gsc_process(afe->gsc, x0, x1, angle_deg, !afe->last_vad, y);
        return y;
    }
#endif
#if CONFIG_DSP_AFE_BSS_ENABLE
    case DSP_AFE_SPATIAL_BSS:
        // Output 0 stands in until E8-T4 chooses the talker's stream (KEHOACH 3.8).
        dsp_afe_bss_process(afe->bss, x0, x1, y, afe->beam[1]);
        return y;
#endif
    default:
        for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
            y[k] = (dsp_spec_cplx_t){0.5f * (x0[k].re + x1[k].re), 0.5f * (x0[k].im + x1[k].im)};
        }
        return y;
    }
}

static void run_ns_stage(dsp_afe_t *afe, dsp_spec_cplx_t *y)
{
    if (afe->ns == NULL) { return; }
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        afe->power[k] = y[k].re * y[k].re + y[k].im * y[k].im;
    }
    const float *echo_power = afe->n_channels > N_MICS ? afe->echo_power : NULL;
    float speech_prob = 0.0f;
    if (afe->ns->process(afe->ns_ctx, afe->ns_state, afe->power, echo_power, afe->gain, &speech_prob) !=
        ESP_OK) {
        return;
    }
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        y[k].re *= afe->gain[k];
        y[k].im *= afe->gain[k];
    }
}

static int8_t level_dbfs(const float *hop)
{
    float energy = 0.0f;
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        energy += hop[i] * hop[i];
    }
    const float db = 10.0f * log10f(energy / GEN_GRID_HOP_SAMPLES);
    if (!(db > INT8_MIN)) { return INT8_MIN; }
    return db >= 0.0f ? 0 : (int8_t)lrintf(db);
}

static int8_t to_int8(float value)
{
    if (value <= INT8_MIN) { return INT8_MIN; }
    return value >= INT8_MAX ? INT8_MAX : (int8_t)lrintf(value);
}

static void to_pcm(const float *hop, int16_t *pcm)
{
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        const float s = hop[i] * PCM_FULL_SCALE;
        pcm[i] = s >= INT16_MAX ? INT16_MAX : s <= INT16_MIN ? INT16_MIN : (int16_t)lrintf(s);
    }
}

static void run_hop(dsp_afe_t *afe, const int16_t *interleaved, dsp_afe_frame_t *out)
{
    bool clipped = false;
    bool ref_silent = false;
    uint16_t flags = afe->gap_pending ? DSP_AFE_FLAG_GAP : 0u;
    to_float(afe, interleaved, &clipped, &ref_silent);
    run_echo_stage(afe, &flags);
    for (size_t m = 0; m < N_MICS; m++) {
        dsp_spec_stft_analyze(afe->analysis[m], afe->pcm[m], afe->bins[m]);
    }
    dsp_spec_cplx_t *y = run_spatial_stage(afe);
    run_ns_stage(afe, y);
    dsp_spec_istft_synthesize(afe->synthesis, y, afe->clean);
    bool speech = false;
#if CONFIG_DSP_AFE_VAD_ENABLE
    dsp_afe_vad_process(afe->vad, afe->clean, &speech);
#endif
    const int8_t level = level_dbfs(afe->clean);
    float gain_db = 0.0f;
#if CONFIG_DSP_AFE_AGC_ENABLE
    dsp_afe_agc_process(afe->agc, afe->clean, speech, &gain_db);
#endif
    to_pcm(afe->clean, out->pcm);
    flags |= clipped ? DSP_AFE_FLAG_CLIPPED : 0u;
    flags |= ref_silent ? DSP_AFE_FLAG_NO_REF : 0u;
    out->seq = afe->seq++;
    out->doa_deg = afe->doa_result.angle_deg;
    out->doa_conf = afe->doa_result.confidence;
    out->vad = speech ? 1u : 0u;
    out->level_dbfs = level;
    out->gain_db = to_int8(gain_db);
    out->flags = flags;
    afe->stats.clipped_hops += clipped ? 1u : 0u;
    afe->gap_pending = false;
    afe->last_vad = speech;
}

esp_err_t dsp_afe_feed(dsp_afe_t *afe, const int16_t *interleaved, size_t frames)
{
    if (afe == NULL || (interleaved == NULL && frames > 0)) { return ESP_ERR_INVALID_ARG; }
    if (frames > (size_t)(DSP_AFE_FIFO_FRAMES - afe->fifo_count)) {
        afe->stats.fifo_overflows++;
        return ESP_ERR_NO_MEM;
    }
    for (size_t f = 0; f < frames; f++) {
        const size_t tail = (afe->fifo_head + afe->fifo_count) % DSP_AFE_FIFO_FRAMES;
        run_hop(afe, interleaved + f * GEN_GRID_HOP_SAMPLES * afe->n_channels, &afe->fifo[tail]);
        afe->fifo_count++;
        afe->stats.hops_in++;
    }
    return ESP_OK;
}

esp_err_t dsp_afe_fetch(dsp_afe_t *afe, dsp_afe_frame_t *out)
{
    if (afe == NULL || out == NULL) { return ESP_ERR_INVALID_ARG; }
    if (afe->fifo_count == 0) { return ESP_ERR_NOT_FOUND; }
    *out = afe->fifo[afe->fifo_head];
    afe->fifo_head = (uint8_t)((afe->fifo_head + 1) % DSP_AFE_FIFO_FRAMES);
    afe->fifo_count--;
    afe->stats.hops_out++;
    return ESP_OK;
}

void dsp_afe_reset(dsp_afe_t *afe)
{
    afe_arena_t arena = afe_arena(afe->hot, afe->hot_bytes);
    afe_take(&arena, sizeof(*afe));
    // Same configuration over the same memory that init accepted, so this walk cannot fail.
    (void)walk(&afe->cfg, afe->n_channels, afe, &arena, WALK_RESET);
    afe->fifo_head = 0;
    afe->fifo_count = 0;
    afe->gap_pending = true;
    afe->last_vad = false;
    afe->doa_result = (dsp_afe_doa_result_t){.angle_deg = ANGLE_UNKNOWN_DEG, .confidence = 0};
    afe->stats.gaps++;
}

esp_err_t dsp_afe_set_param(dsp_afe_t *afe, dsp_afe_param_t param, float value)
{
    if (afe == NULL || !param_ok(param, value)) { return ESP_ERR_INVALID_ARG; }
    switch (param) {
    case DSP_AFE_PARAM_NS_FLOOR_DB:
        afe->cfg.ns_floor_db = value;
        afe->omlsa.floor_db = value;
        return ESP_OK;
    case DSP_AFE_PARAM_AGC_TARGET_DBFS: afe->cfg.agc_target_dbfs = value;
#if CONFIG_DSP_AFE_AGC_ENABLE
        dsp_afe_agc_set_target(afe->agc, value);
#endif
        return ESP_OK;
    case DSP_AFE_PARAM_VAD_AGGRESSIVENESS: {
        afe->cfg.vad_aggressiveness = (uint8_t)value;
#if CONFIG_DSP_AFE_VAD_ENABLE
        const dsp_afe_vad_config_t vad = vad_config(&afe->cfg);
        return dsp_afe_vad_init(&afe->vad, &vad, afe->vad_mem, afe->vad_bytes);
#else
        return ESP_OK;
#endif
    }
    default: return ESP_ERR_INVALID_ARG;
    }
}

void dsp_afe_stats(const dsp_afe_t *afe, dsp_afe_stats_t *out)
{
    *out = afe->stats;
}
