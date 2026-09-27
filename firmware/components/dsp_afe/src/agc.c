#include "dsp_afe/agc.h"

#include <math.h>
#include <string.h>

#include "afe_internal.h"
#include "gen_grid.h"

#define HOP GEN_GRID_HOP_SAMPLES
#define MS_PER_S 1000.0

typedef struct {
    float *values;
    uint32_t n;
    uint32_t oldest;    // next slot to write, so the oldest value
    uint32_t under_one; // a window of ones needs neither minimum nor sum
} window_t;

struct dsp_afe_agc_s {
    float target_power;
    float level_keep;
    float gate;
    float fall;
    float up;
    float down;
    float gain_min;
    float gain_max;
    float ceiling;
    float release_step;
    float speech_power;
    float gain;
    float last_held;
    uint32_t lookahead;
    uint32_t delay_at;
    float *delay;
    window_t need;
    window_t held;
};

static bool config_ok(const dsp_afe_agc_config_t *cfg)
{
    return cfg != NULL && cfg->target_dbfs <= 0.0f && cfg->gain_min_db <= cfg->gain_max_db &&
           cfg->level_tau_s > 0.0f && cfg->level_gate_db > 0.0f && cfg->level_fall_db_per_s >= 0.0f &&
           cfg->up_db_per_s > 0.0f && cfg->down_db_per_s > 0.0f && cfg->limit_dbfs <= 0.0f &&
           cfg->lookahead_ms >= 0.0f && cfg->release_ms > 0.0f;
}

static uint32_t lookahead_samples(const dsp_afe_agc_config_t *cfg)
{
    return (uint32_t)lrintf(cfg->lookahead_ms * (float)GEN_GRID_SAMPLE_RATE_HZ / (float)MS_PER_S);
}

// Constants only, once at init: double then one rounding, as srpipe.dsp.afe.agc makes them.
static float db_to_amplitude(float db)
{
    return (float)pow(10.0, (double)db / 20.0);
}

static float db_to_power(float db)
{
    return (float)pow(10.0, (double)db / 10.0);
}

static size_t layout(const dsp_afe_agc_config_t *cfg, afe_arena_t *arena, dsp_afe_agc_t **st)
{
    const uint32_t lookahead = lookahead_samples(cfg);
    *st = afe_take(arena, sizeof(struct dsp_afe_agc_s));
    float *delay = afe_take(arena, lookahead * sizeof(float));
    float *need = afe_take(arena, (lookahead + 1) * sizeof(float));
    float *held = afe_take(arena, (lookahead + 1) * sizeof(float));
    if (*st != NULL && need != NULL && held != NULL) {
        memset(*st, 0, sizeof(**st));
        (*st)->lookahead = lookahead;
        (*st)->delay = delay;
        (*st)->need.values = need;
        (*st)->held.values = held;
    }
    return arena->used;
}

size_t dsp_afe_agc_workspace_bytes(const dsp_afe_agc_config_t *cfg)
{
    if (!config_ok(cfg)) { return 0; }
    afe_arena_t sizing = afe_arena(NULL, 0);
    dsp_afe_agc_t *unused = NULL;
    return AFE_ALIGN_BYTES + layout(cfg, &sizing, &unused);
}

static void window_init(window_t *w, uint32_t n)
{
    w->n = n;
    w->oldest = 0;
    w->under_one = 0;
    for (uint32_t i = 0; i < n; i++) {
        w->values[i] = 1.0f;
    }
}

esp_err_t dsp_afe_agc_init(dsp_afe_agc_t **out, const dsp_afe_agc_config_t *cfg, void *mem, size_t bytes)
{
    if (out == NULL || mem == NULL || !config_ok(cfg)) { return ESP_ERR_INVALID_ARG; }
    afe_arena_t arena = afe_arena(mem, bytes);
    dsp_afe_agc_t *st = NULL;
    if (layout(cfg, &arena, &st) > arena.cap || st == NULL) { return ESP_ERR_INVALID_SIZE; }
    const double hop_s = (double)HOP / GEN_GRID_SAMPLE_RATE_HZ;
    st->level_keep = (float)exp(-hop_s / (double)cfg->level_tau_s);
    st->up = db_to_amplitude((float)((double)cfg->up_db_per_s * hop_s));
    st->down = db_to_amplitude((float)(-(double)cfg->down_db_per_s * hop_s));
    st->gate = db_to_power(-cfg->level_gate_db);
    st->fall = db_to_power((float)(-(double)cfg->level_fall_db_per_s * hop_s));
    st->gain_min = db_to_amplitude(cfg->gain_min_db);
    st->gain_max = db_to_amplitude(cfg->gain_max_db);
    st->ceiling = db_to_amplitude(cfg->limit_dbfs);
    st->release_step = (float)(1.0 / ((double)cfg->release_ms * GEN_GRID_SAMPLE_RATE_HZ / MS_PER_S));
    st->target_power = db_to_power(cfg->target_dbfs);
    // Start the level at the target less the most gain, so quiet speech passes the gate at once.
    st->speech_power = st->target_power / (st->gain_max * st->gain_max);
    st->gain = 1.0f;
    st->last_held = 1.0f;
    if (st->lookahead > 0) { memset(st->delay, 0, st->lookahead * sizeof(float)); }
    window_init(&st->need, st->lookahead + 1);
    window_init(&st->held, st->lookahead + 1);
    *out = st;
    return ESP_OK;
}

static void window_push(window_t *w, float v)
{
    if (w->values[w->oldest] < 1.0f) { w->under_one--; }
    w->values[w->oldest] = v;
    if (v < 1.0f) { w->under_one++; }
    w->oldest = w->oldest + 1 == w->n ? 0 : w->oldest + 1;
}

static float window_min(const window_t *w)
{
    if (w->under_one == 0) { return 1.0f; }
    float low = w->values[w->oldest];
    for (uint32_t i = 0; i < w->n; i++) {
        low = fminf(low, w->values[i]);
    }
    return low;
}

static float window_mean(const window_t *w)
{
    if (w->under_one == 0) { return 1.0f; }
    float total = 0.0f;
    for (uint32_t i = 0, at = w->oldest; i < w->n; i++, at = at + 1 == w->n ? 0 : at + 1) {
        total = total + w->values[at];
    }
    return total / (float)w->n;
}

static void slow_gain(dsp_afe_agc_t *st, const float *hop)
{
    float energy = 0.0f;
    for (size_t i = 0; i < HOP; i++) {
        energy = energy + hop[i] * hop[i];
    }
    const float power = energy / (float)HOP;
    if (power >= st->gate * st->speech_power) {
        st->speech_power = st->level_keep * st->speech_power + (1.0f - st->level_keep) * power;
    } else {
        st->speech_power = st->fall * st->speech_power;
    }
    float wanted = st->gain_max;
    if (st->speech_power > 0.0f) { wanted = sqrtf(st->target_power / st->speech_power); }
    wanted = fminf(fmaxf(wanted, st->gain_min), st->gain_max);
    if (st->gain < wanted) {
        st->gain = fminf(st->gain * st->up, wanted);
    } else if (st->gain > wanted) {
        st->gain = fmaxf(st->gain * st->down, wanted);
    }
}

esp_err_t dsp_afe_agc_process(dsp_afe_agc_t *st, float *hop, bool speech, float *gain_db)
{
    if (st == NULL || hop == NULL) { return ESP_ERR_INVALID_ARG; }
    if (speech) { slow_gain(st, hop); }
    for (size_t i = 0; i < HOP; i++) {
        const float s = st->gain * hop[i];
        const float magnitude = fabsf(s);
        window_push(&st->need, magnitude > st->ceiling ? st->ceiling / magnitude : 1.0f);
        const float held = fminf(window_min(&st->need), st->last_held + st->release_step);
        st->last_held = held;
        window_push(&st->held, held);
        float delayed = s;
        if (st->lookahead > 0) {
            delayed = st->delay[st->delay_at];
            st->delay[st->delay_at] = s;
            st->delay_at = st->delay_at + 1 == st->lookahead ? 0 : st->delay_at + 1;
        }
        hop[i] = delayed * window_mean(&st->held);
    }
    if (gain_db != NULL) { *gain_db = 20.0f * log10f(st->gain); }
    return ESP_OK;
}

void dsp_afe_agc_set_target(dsp_afe_agc_t *st, float target_dbfs)
{
    st->target_power = db_to_power(target_dbfs);
}
