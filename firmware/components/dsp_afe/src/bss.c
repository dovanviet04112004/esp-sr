#include "dsp_afe/bss.h"

#include <math.h>
#include <string.h>

#include "afe_internal.h"
#include "gen_grid.h"

#define ANGLES 181 // every whole degree doa reports, 0 .. 180
#define DEG_PER_HALF_TURN 180.0
#define LOGISTIC_REACH 40.0 // past this the logistic is 0 or 1 in float
#define EXP_SPLIT 8.0       // e^y as (e^(y/8))^8 keeps the series within |x| <= 5
#define EXP_SERIES_REACH 5.0
#define GATE_START 0.5f
#define ANGLE_UNKNOWN_DEG (-1)
#define SEED_LOW_PCT 10 // k-means seeds at the 10th .. 90th percentiles
#define SEED_SPAN_PCT 80

typedef struct {
    size_t window; // updates the ring holds
    size_t first;  // updates ahead of the first clustering
    uint32_t recheck_hops;
    uint32_t forget_hops;
    uint32_t history; // hops of held angles numbered_back reaches
} span_t;

struct dsp_afe_bss_s {
    dsp_afe_bss_config_t cfg;
    span_t span;
    double slope;
    float delay[ANGLES]; // samples of delay of each whole degree
    float rise[ANGLES];  // broadside split's share of number 1
    float keep_delay;
    uint8_t side; // keep_deg's broadside number
    float rate;
    float half_gap;
    float share_hi;
    int16_t *ring;
    int16_t *held; // held angle of each of the last history + 1 hops
    int16_t last_angle;
    size_t head;
    size_t filled;
    uint32_t hist[ANGLES];
    uint32_t hop;
    uint32_t since;
    uint8_t seats;
    uint8_t pending;
    uint8_t agree;
    float centres[DSP_AFE_BSS_SEATS_MAX]; // samples of delay, rising
    float offsets[DSP_AFE_BSS_SEATS_MAX - 1];
    uint32_t seen[DSP_AFE_BSS_SEATS_MAX]; // hop of each seat's last update
    uint32_t votes[DSP_AFE_BSS_SEATS_MAX];
    uint8_t number[DSP_AFE_BSS_SEATS_MAX];
    float gate;
    float shares[DSP_AFE_BSS_SEATS_MAX]; // gate of each number at the last search
};

static double speech_s(const dsp_afe_bss_config_t *cfg)
{
    return (double)GEN_GRID_HOP_SAMPLES / GEN_GRID_SAMPLE_RATE_HZ * cfg->update_every_hops;
}

static bool config_ok(const dsp_afe_bss_config_t *cfg)
{
    if (cfg == NULL) { return false; }
    const bool ok = (cfg->mode == DSP_AFE_BSS_FIXED || cfg->mode == DSP_AFE_BSS_LEARN) &&
                    (cfg->keep_rule == DSP_AFE_BSS_KEEP_ANGLE || cfg->keep_rule == DSP_AFE_BSS_KEEP_MOST) &&
                    cfg->keep_deg >= 0.0f && cfg->keep_deg <= ANGLES - 1 && cfg->gate_slope > 0.0f &&
                    cfg->seats_max >= 2 && cfg->seats_max <= DSP_AFE_BSS_SEATS_MAX &&
                    cfg->decision_s > 0.0f && cfg->recheck_s > 0.0f && cfg->first_speech_s > 0.0f &&
                    cfg->first_speech_s <= cfg->decision_s && cfg->kmeans_rounds >= 1 && cfg->confirm >= 1 &&
                    cfg->occupancy_min >= 0.0f && cfg->occupancy_min < 1.0f && cfg->valley_max > 0.0f &&
                    cfg->valley_width > 0.0f && cfg->valley_width < 0.5f && cfg->min_gap > 0.0f &&
                    cfg->boundary_share_min >= 0.0f && cfg->boundary_share_min <= 0.5f &&
                    cfg->boundary_follow >= 0.0f && cfg->boundary_follow <= 1.0f && cfg->track_tau_s > 0.0f &&
                    cfg->track_gate > 0.0f && cfg->seat_forget_s > 0.0f && cfg->update_every_hops >= 1 &&
                    cfg->spacing_m > 0.0f && cfg->speed_of_sound_m_s > 0.0f && cfg->history_s >= 0.0f;
    return ok && speech_s(cfg) / (double)cfg->track_tau_s <= EXP_SERIES_REACH;
}

static size_t rounded(double x)
{
    const double r = floor(x + 0.5);
    return r < 1.0 ? 1u : (size_t)r;
}

static span_t span_of(const dsp_afe_bss_config_t *cfg)
{
    const double per_update = speech_s(cfg);
    const double hop_s = (double)GEN_GRID_HOP_SAMPLES / GEN_GRID_SAMPLE_RATE_HZ;
    span_t s = {
        .window = rounded((double)cfg->decision_s / per_update),
        .first = rounded((double)cfg->first_speech_s / per_update),
        .recheck_hops = (uint32_t)rounded((double)cfg->recheck_s / hop_s),
        .forget_hops = (uint32_t)rounded((double)cfg->seat_forget_s / hop_s),
        .history = (uint32_t)floor((double)cfg->history_s / hop_s + 0.5),
    };
    if (s.first > s.window) { s.first = s.window; }
    return s;
}

static dsp_afe_bss_t *carve(afe_arena_t *a, const span_t *s)
{
    dsp_afe_bss_t *st = afe_take(a, sizeof(*st));
    int16_t *ring = afe_take(a, s->window * sizeof(int16_t));
    int16_t *held = afe_take(a, (s->history + 1u) * sizeof(int16_t));
    if (st != NULL) {
        st->ring = ring;
        st->held = held;
    }
    return st;
}

size_t dsp_afe_bss_workspace_bytes(const dsp_afe_bss_config_t *cfg)
{
    if (!config_ok(cfg)) { return 0; }
    const span_t s = span_of(cfg);
    afe_arena_t sizing = afe_arena(NULL, 0);
    carve(&sizing, &s);
    return AFE_ALIGN_BYTES + sizing.used;
}

static double logistic(double y)
{
    if (y > LOGISTIC_REACH) { y = LOGISTIC_REACH; }
    if (y < -LOGISTIC_REACH) { y = -LOGISTIC_REACH; }
    double e = afe_exp_series(-y / EXP_SPLIT);
    e = e * e;
    e = e * e;
    e = e * e;
    return 1.0 / (1.0 + e);
}

// Share of the seats above cut j - 1: 1 for the first seat, 0 past the last cut.
static float above(const dsp_afe_bss_t *st, float delay, const float *cuts, size_t n_cuts, size_t j)
{
    if (j == 0) { return 1.0f; }
    if (j > n_cuts) { return 0.0f; }
    return (float)logistic(2.0 * st->slope * ((double)delay - (double)cuts[j - 1]));
}

static size_t nearest(float value, const float *centres, size_t n)
{
    size_t at = 0;
    float best = 0.0f;
    for (size_t i = 0; i < n; i++) {
        const float d = fabsf(value - centres[i]);
        if (i == 0 || d < best) {
            best = d;
            at = i;
        }
    }
    return at;
}

void dsp_afe_bss_reset(dsp_afe_bss_t *st)
{
    st->head = 0;
    st->filled = 0;
    memset(st->hist, 0, sizeof(st->hist));
    st->hop = 0;
    st->since = 0;
    st->seats = 0;
    st->pending = 0;
    st->agree = 0;
    memset(st->centres, 0, sizeof(st->centres));
    memset(st->offsets, 0, sizeof(st->offsets));
    memset(st->seen, 0, sizeof(st->seen));
    memset(st->votes, 0, sizeof(st->votes));
    memset(st->number, 0, sizeof(st->number));
    st->gate = GATE_START;
    memset(st->shares, 0, sizeof(st->shares));
    st->shares[0] = GATE_START;
    st->shares[1] = GATE_START;
    st->last_angle = ANGLE_UNKNOWN_DEG;
    for (size_t h = 0; h <= st->span.history; h++) {
        st->held[h] = ANGLE_UNKNOWN_DEG;
    }
}

esp_err_t dsp_afe_bss_init(dsp_afe_bss_t **out, const dsp_afe_bss_config_t *cfg, void *mem, size_t bytes)
{
    if (out == NULL || mem == NULL || !config_ok(cfg)) { return ESP_ERR_INVALID_ARG; }
    const span_t s = span_of(cfg);
    afe_arena_t arena = afe_arena(mem, bytes);
    dsp_afe_bss_t *st = carve(&arena, &s);
    if (st == NULL || arena.used > arena.cap) { return ESP_ERR_INVALID_SIZE; }
    int16_t *ring = st->ring;
    int16_t *held = st->held;
    memset(st, 0, sizeof(*st));
    st->ring = ring;
    st->held = held;
    st->cfg = *cfg;
    st->span = s;
    st->slope = (double)cfg->gate_slope;
    const double tau_max = (double)cfg->spacing_m * GEN_GRID_SAMPLE_RATE_HZ / (double)cfg->speed_of_sound_m_s;
    for (size_t d = 0; d < ANGLES; d++) {
        st->delay[d] = (float)(tau_max * afe_cos_series((double)d * AFE_PI / DEG_PER_HALF_TURN));
    }
    st->keep_delay = (float)(tau_max * afe_cos_series((double)cfg->keep_deg * AFE_PI / DEG_PER_HALF_TURN));
    st->side = st->keep_delay > 0.0f ? 1u : 0u;
    const float axis[1] = {0.0f};
    for (size_t d = 0; d < ANGLES; d++) {
        st->rise[d] = above(st, st->delay[d], axis, 1, 1) - above(st, st->delay[d], axis, 1, 2);
    }
    st->rate = (float)(1.0 - afe_exp_series(-speech_s(cfg) / (double)cfg->track_tau_s));
    st->half_gap = 0.5f * cfg->min_gap;
    st->share_hi = 1.0f - cfg->boundary_share_min;
    dsp_afe_bss_reset(st);
    *out = st;
    return ESP_OK;
}

static void count(dsp_afe_bss_t *st, int16_t angle)
{
    if (st->filled == st->span.window) {
        st->hist[st->ring[st->head]]--;
    } else {
        st->filled++;
    }
    st->ring[st->head] = angle;
    st->hist[angle]++;
    st->head = (st->head + 1) % st->span.window;
}

static void track(dsp_afe_bss_t *st, float delay)
{
    float *c = st->centres;
    const size_t j = nearest(delay, c, st->seats);
    if (!(fabsf(delay - c[j]) <= st->cfg.track_gate)) { return; }
    c[j] = c[j] + st->rate * (delay - c[j]);
    for (size_t i = 0; i + 1 < st->seats; i++) {
        if (c[i + 1] - c[i] < st->cfg.min_gap) {
            const float mid = 0.5f * (c[i] + c[i + 1]);
            c[i] = mid - st->half_gap;
            c[i + 1] = mid + st->half_gap;
        }
    }
    st->seen[j] = st->hop;
    st->votes[j]++;
}

// A seat's two boundaries merge into one at the midpoint of its neighbours once it goes.
static void forget(dsp_afe_bss_t *st)
{
    size_t i = 0;
    while (i < st->seats && st->seats >= 2) {
        if (st->hop - st->seen[i] < st->span.forget_hops) {
            i++;
            continue;
        }
        const size_t last = st->seats - 1u;
        if (i > 0 && i < last) { st->offsets[i - 1] = 0.0f; }
        for (size_t j = i; j < last; j++) {
            st->centres[j] = st->centres[j + 1];
            st->seen[j] = st->seen[j + 1];
            st->votes[j] = st->votes[j + 1];
            st->number[j] = st->number[j + 1];
        }
        for (size_t j = i < last ? i : last - 1u; j + 1 < last; j++) {
            st->offsets[j] = st->offsets[j + 1];
        }
        st->seats--;
    }
    if (st->seats < 2) { st->seats = 0; }
}

static uint32_t density(const dsp_afe_bss_t *st, float at, float width)
{
    uint32_t n = 0;
    for (int d = ANGLES - 1; d >= 0; d--) {
        if (st->hist[d] > 0 && fabsf(st->delay[d] - at) < width) { n += st->hist[d]; }
    }
    return n;
}

static void sort_rising(float *c, size_t k)
{
    for (size_t i = 1; i < k; i++) {
        const float v = c[i];
        size_t j = i;
        while (j > 0 && c[j - 1] > v) {
            c[j] = c[j - 1];
            j--;
        }
        c[j] = v;
    }
}

// False when the window does not hold k seats; offsets are each boundary's distance off its midpoint.
static bool kmeans(const dsp_afe_bss_t *st, size_t k, float *c, float *offsets)
{
    const size_t total = st->filled;
    for (size_t i = 0; i < k; i++) {
        const size_t target = total * (SEED_LOW_PCT * (k - 1) + SEED_SPAN_PCT * i) / (100u * (k - 1));
        size_t run = 0;
        for (int d = ANGLES - 1; d >= 0; d--) {
            run += st->hist[d];
            if (st->hist[d] > 0 && run > target) {
                c[i] = st->delay[d];
                break;
            }
        }
    }
    float sums[DSP_AFE_BSS_SEATS_MAX];
    size_t counts[DSP_AFE_BSS_SEATS_MAX];
    for (uint8_t round = 0; round < st->cfg.kmeans_rounds; round++) {
        memset(sums, 0, sizeof(sums));
        memset(counts, 0, sizeof(counts));
        for (int d = ANGLES - 1; d >= 0; d--) {
            if (st->hist[d] == 0) { continue; }
            const size_t j = nearest(st->delay[d], c, k);
            sums[j] = sums[j] + (float)st->hist[d] * st->delay[d];
            counts[j] += st->hist[d];
        }
        for (size_t j = 0; j < k; j++) {
            if (counts[j] > 0) { c[j] = sums[j] / (float)counts[j]; }
        }
        sort_rising(c, k);
    }
    memset(sums, 0, sizeof(sums));
    memset(counts, 0, sizeof(counts));
    for (int d = ANGLES - 1; d >= 0; d--) {
        if (st->hist[d] == 0) { continue; }
        const size_t j = nearest(st->delay[d], c, k);
        const float off = st->delay[d] - c[j];
        sums[j] = sums[j] + (float)st->hist[d] * (off * off);
        counts[j] += st->hist[d];
    }
    const float least = st->cfg.occupancy_min * (float)total;
    float spread[DSP_AFE_BSS_SEATS_MAX];
    for (size_t j = 0; j < k; j++) {
        if ((float)counts[j] < least) { return false; }
        spread[j] = counts[j] > 1 ? sqrtf(sums[j] / (float)counts[j]) : 0.0f;
    }
    for (size_t j = 0; j + 1 < k; j++) {
        const float gap = c[j + 1] - c[j];
        const float mid = 0.5f * (c[j] + c[j + 1]);
        const float width = st->cfg.valley_width * gap;
        const float peaks = 0.5f * (float)(density(st, c[j], width) + density(st, c[j + 1], width));
        if (gap < st->cfg.min_gap || !((float)density(st, mid, width) < st->cfg.valley_max * peaks)) {
            return false;
        }
        const float a = spread[j];
        const float b = spread[j + 1];
        const float both = a + b;
        float bound = mid;
        if (both > 0.0f) {
            const float share = a / both;
            if (st->cfg.boundary_share_min <= share && share <= st->share_hi) {
                bound = (c[j] * b + c[j + 1] * a) / both;
            } else {
                const float w =
                    share < st->cfg.boundary_share_min ? st->cfg.boundary_share_min : st->share_hi;
                bound = c[j] + w * gap;
            }
        }
        offsets[j] = bound - mid;
    }
    return true;
}

static void window_votes(dsp_afe_bss_t *st)
{
    memset(st->votes, 0, sizeof(st->votes));
    for (int d = ANGLES - 1; d >= 0; d--) {
        if (st->hist[d] > 0) { st->votes[nearest(st->delay[d], st->centres, st->seats)] += st->hist[d]; }
    }
}

static bool holds(const int8_t *numbers, size_t n, int8_t value)
{
    for (size_t i = 0; i < n; i++) {
        if (numbers[i] == value) { return true; }
    }
    return false;
}

// Held seats pass their numbers, by rising delay, to the nearest free centre (KEHOACH 3.8).
static void renumber(dsp_afe_bss_t *st, const float *centres, uint8_t found)
{
    int8_t numbers[DSP_AFE_BSS_SEATS_MAX];
    memset(numbers, -1, sizeof(numbers));
    for (size_t i = 0; i < st->seats; i++) {
        size_t at = 0;
        float best = 0.0f;
        bool any = false;
        for (size_t j = 0; j < found; j++) {
            const float d = fabsf(centres[j] - st->centres[i]);
            if (numbers[j] < 0 && (!any || d < best)) {
                best = d;
                at = j;
                any = true;
            }
        }
        numbers[at] = (int8_t)st->number[i];
    }
    int8_t next = 0;
    for (size_t j = 0; j < found; j++) {
        if (numbers[j] >= 0) { continue; }
        while (holds(numbers, found, next)) {
            next++;
        }
        numbers[j] = next;
    }
    for (size_t j = 0; j < found; j++) {
        st->number[j] = (uint8_t)numbers[j];
    }
}

// Seats grow once confirm clusterings agree and never shrink here: a seat goes only by forget.
static void cluster(dsp_afe_bss_t *st)
{
    float c[DSP_AFE_BSS_SEATS_MAX];
    float o[DSP_AFE_BSS_SEATS_MAX - 1];
    float fit_c[DSP_AFE_BSS_SEATS_MAX] = {0};
    float fit_o[DSP_AFE_BSS_SEATS_MAX - 1] = {0};
    uint8_t found = 1;
    for (uint8_t k = 2; k <= st->cfg.seats_max; k++) {
        if (!kmeans(st, k, c, o)) { break; }
        found = k;
        memcpy(fit_c, c, sizeof(c));
        memcpy(fit_o, o, sizeof(o));
    }
    const uint8_t current = st->seats > 1 ? st->seats : 1;
    if (found > current) {
        st->agree = found == st->pending ? (uint8_t)(st->agree + 1) : 1;
        st->pending = found;
        if (st->agree >= st->cfg.confirm) {
            renumber(st, fit_c, found);
            st->seats = found;
            memcpy(st->centres, fit_c, found * sizeof(float));
            memcpy(st->offsets, fit_o, (found - 1u) * sizeof(float));
            for (size_t j = 0; j < found; j++) {
                st->seen[j] = st->hop;
            }
            window_votes(st);
            st->pending = 0;
            st->agree = 0;
        }
        return;
    }
    st->pending = 0;
    st->agree = 0;
    if (found == current && current >= 2) {
        for (size_t j = 0; j + 1 < current; j++) {
            st->offsets[j] = st->offsets[j] + st->cfg.boundary_follow * (fit_o[j] - st->offsets[j]);
        }
    }
}

static size_t keep_seat(const dsp_afe_bss_t *st)
{
    if (st->cfg.keep_rule == DSP_AFE_BSS_KEEP_ANGLE) {
        return nearest(st->keep_delay, st->centres, st->seats);
    }
    size_t at = 0;
    for (size_t j = 1; j < st->seats; j++) {
        if (st->votes[j] > st->votes[at]) { at = j; }
    }
    return at;
}

// The kept seat's gate; shares gets the gate of every number.
static float gates_of(const dsp_afe_bss_t *st, int16_t angle, float *shares)
{
    memset(shares, 0, DSP_AFE_BSS_SEATS_MAX * sizeof(float));
    if (st->seats < 2) {
        shares[0] = 1.0f - st->rise[angle];
        shares[1] = st->rise[angle];
        return shares[st->side];
    }
    float cuts[DSP_AFE_BSS_SEATS_MAX - 1];
    for (size_t j = 0; j + 1 < st->seats; j++) {
        cuts[j] = 0.5f * (st->centres[j] + st->centres[j + 1]) + st->offsets[j];
    }
    float up[DSP_AFE_BSS_SEATS_MAX + 1];
    for (size_t j = 0; j <= st->seats; j++) {
        up[j] = above(st, st->delay[angle], cuts, st->seats - 1u, j);
    }
    for (size_t j = 0; j < st->seats; j++) {
        shares[st->number[j]] = up[j] - up[j + 1];
    }
    const size_t keep = keep_seat(st);
    return up[keep] - up[keep + 1];
}

esp_err_t dsp_afe_bss_process(dsp_afe_bss_t *st, const dsp_spec_cplx_t *x0, const dsp_spec_cplx_t *x1,
                              int16_t angle_deg, uint8_t confidence, bool searched, dsp_spec_cplx_t *y0,
                              dsp_spec_cplx_t *y1)
{
    if (st == NULL || x0 == NULL || x1 == NULL || y0 == NULL || y1 == NULL) { return ESP_ERR_INVALID_ARG; }
    const bool counted = searched && angle_deg >= 0 && angle_deg < ANGLES && confidence >= st->cfg.conf_min;
    if (st->cfg.mode == DSP_AFE_BSS_LEARN) {
        if (counted) {
            count(st, angle_deg);
            if (st->seats >= 2) { track(st, st->delay[angle_deg]); }
        }
        forget(st);
        st->since++;
        if (st->since >= st->span.recheck_hops && st->filled >= st->span.first) {
            st->since = 0;
            cluster(st);
        }
    }
    if (counted) {
        st->gate = gates_of(st, angle_deg, st->shares);
        st->last_angle = angle_deg;
    }
    st->held[st->hop % (st->span.history + 1u)] = st->last_angle;
    st->hop++;
    const float keep = st->gate;
    const float other = 1.0f - keep;
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        const float re = 0.5f * (x0[k].re + x1[k].re);
        const float im = 0.5f * (x0[k].im + x1[k].im);
        y0[k] = (dsp_spec_cplx_t){re * keep, im * keep};
        y1[k] = (dsp_spec_cplx_t){re * other, im * other};
    }
    return ESP_OK;
}

void dsp_afe_bss_directions(const dsp_afe_bss_t *st, int16_t angle_deg[2])
{
    angle_deg[0] = ANGLE_UNKNOWN_DEG;
    angle_deg[1] = ANGLE_UNKNOWN_DEG;
    if (st->seats < 2) { return; }
    const size_t keep = keep_seat(st);
    size_t other = keep == 0 ? 1u : 0u;
    for (size_t j = 0; j < st->seats; j++) {
        if (j != keep &&
            fabsf(st->centres[keep] - st->centres[j]) < fabsf(st->centres[keep] - st->centres[other])) {
            other = j;
        }
    }
    angle_deg[0] = (int16_t)nearest(st->centres[keep], st->delay, ANGLES);
    angle_deg[1] = (int16_t)nearest(st->centres[other], st->delay, ANGLES);
}

void dsp_afe_bss_state(const dsp_afe_bss_t *st, uint8_t *seats, float *gate)
{
    *seats = st->seats;
    *gate = st->gate;
}

void dsp_afe_bss_numbered(const dsp_afe_bss_t *st, float gate[DSP_AFE_BSS_SEATS_MAX],
                          int16_t angle_deg[DSP_AFE_BSS_SEATS_MAX])
{
    memcpy(gate, st->shares, sizeof(st->shares));
    for (size_t j = 0; j < DSP_AFE_BSS_SEATS_MAX; j++) {
        angle_deg[j] = ANGLE_UNKNOWN_DEG;
    }
    if (st->seats < 2) { return; }
    for (size_t j = 0; j < st->seats; j++) {
        angle_deg[st->number[j]] = (int16_t)nearest(st->centres[j], st->delay, ANGLES);
    }
}

esp_err_t dsp_afe_bss_numbered_back(const dsp_afe_bss_t *st, uint32_t back_hops,
                                    float gate[DSP_AFE_BSS_SEATS_MAX], uint8_t *kept)
{
    if (st == NULL || gate == NULL || kept == NULL || back_hops > st->span.history) {
        return ESP_ERR_INVALID_ARG;
    }
    *kept = st->seats >= 2 ? st->number[keep_seat(st)] : st->side;
    const int16_t angle = st->hop > back_hops ? st->held[(st->hop - 1u - back_hops) % (st->span.history + 1u)]
                                              : ANGLE_UNKNOWN_DEG;
    if (angle < 0) {
        memset(gate, 0, DSP_AFE_BSS_SEATS_MAX * sizeof(float));
        gate[0] = GATE_START;
        gate[1] = GATE_START;
        return ESP_OK;
    }
    (void)gates_of(st, angle, gate);
    return ESP_OK;
}
