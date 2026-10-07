#include "dsp_afe/agc.h"

#include <math.h>
#include <string.h>

#include "afe_internal.h"
#include "gen_grid.h"

#define HOP GEN_GRID_HOP_SAMPLES
#define MS_PER_S 1000.0

// Minimum of the last n values: (index, value) pairs whose values rise from front to back.
typedef struct {
    uint32_t *index;
    float *value;
    uint32_t n;
    uint32_t front;
    uint32_t size;
    uint32_t count;  // pushes so far, wrapping; ages are taken modulo 2^32
    uint32_t pushed; // saturates at n; the window starts as ones
} sliding_min_t;

// Mean of the last n values: a running sum, summed afresh each hop so rounding cannot build up.
typedef struct {
    float *values;
    uint32_t n;
    uint32_t oldest;    // next slot to write, so the oldest value
    uint32_t under_one; // while 0 the mean is exactly 1
    float total;
} box_mean_t;

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
    sliding_min_t need;
    box_mean_t held;
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
    uint32_t *need_index = afe_take(arena, (lookahead + 1) * sizeof(uint32_t));
    float *need_value = afe_take(arena, (lookahead + 1) * sizeof(float));
    float *held = afe_take(arena, (lookahead + 1) * sizeof(float));
    if (*st != NULL && need_index != NULL && need_value != NULL && held != NULL) {
        memset(*st, 0, sizeof(**st));
        (*st)->lookahead = lookahead;
        (*st)->delay = delay;
        (*st)->need.index = need_index;
        (*st)->need.value = need_value;
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

static void box_mean_init(box_mean_t *b, uint32_t n)
{
    b->n = n;
    b->oldest = 0;
    b->under_one = 0;
    for (uint32_t i = 0; i < n; i++) {
        b->values[i] = 1.0f;
    }
    b->total = (float)n;
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
    st->need.n = st->lookahead + 1;
    dsp_afe_agc_reset(st);
    *out = st;
    return ESP_OK;
}

void dsp_afe_agc_reset(dsp_afe_agc_t *st)
{
    // Start the level at the target less the most gain, so quiet speech passes the gate at once.
    st->speech_power = st->target_power / (st->gain_max * st->gain_max);
    st->gain = 1.0f;
    st->last_held = 1.0f;
    st->delay_at = 0;
    if (st->lookahead > 0) { memset(st->delay, 0, st->lookahead * sizeof(float)); }
    st->need.front = 0;
    st->need.size = 0;
    st->need.count = 0;
    st->need.pushed = 0;
    box_mean_init(&st->held, st->lookahead + 1);
}

static float sliding_min_push(sliding_min_t *m, float v)
{
    while (m->size > 0 && m->value[(m->front + m->size - 1) % m->n] >= v) {
        m->size--;
    }
    const uint32_t back = (m->front + m->size) % m->n;
    m->index[back] = m->count;
    m->value[back] = v;
    m->size++;
    if (m->count - m->index[m->front] >= m->n) {
        m->front = m->front + 1 == m->n ? 0 : m->front + 1;
        m->size--;
    }
    m->count++;
    if (m->pushed < m->n) { m->pushed++; }
    return m->pushed < m->n ? fminf(m->value[m->front], 1.0f) : m->value[m->front];
}

static void box_mean_resum(box_mean_t *b)
{
    float total = 0.0f;
    for (uint32_t i = 0, at = b->oldest; i < b->n; i++, at = at + 1 == b->n ? 0 : at + 1) {
        total = total + b->values[at];
    }
    b->total = total;
}

static float box_mean_push(box_mean_t *b, float v)
{
    const float old = b->values[b->oldest];
    if (old < 1.0f) { b->under_one--; }
    b->values[b->oldest] = v;
    if (v < 1.0f) { b->under_one++; }
    b->oldest = b->oldest + 1 == b->n ? 0 : b->oldest + 1;
    b->total = (b->total - old) + v;
    return b->under_one == 0 ? 1.0f : b->total / (float)b->n;
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
    box_mean_resum(&st->held);
    for (size_t i = 0; i < HOP; i++) {
        const float s = st->gain * hop[i];
        const float magnitude = fabsf(s);
        const float lowest =
            sliding_min_push(&st->need, magnitude > st->ceiling ? st->ceiling / magnitude : 1.0f);
        const float held = fminf(lowest, st->last_held + st->release_step);
        st->last_held = held;
        const float mean = box_mean_push(&st->held, held);
        float delayed = s;
        if (st->lookahead > 0) {
            delayed = st->delay[st->delay_at];
            st->delay[st->delay_at] = s;
            st->delay_at = st->delay_at + 1 == st->lookahead ? 0 : st->delay_at + 1;
        }
        hop[i] = delayed * mean;
    }
    if (gain_db != NULL) { *gain_db = 20.0f * log10f(st->gain); }
    return ESP_OK;
}

void dsp_afe_agc_set_target(dsp_afe_agc_t *st, float target_dbfs)
{
    st->target_power = db_to_power(target_dbfs);
}
