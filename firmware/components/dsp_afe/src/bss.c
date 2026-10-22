#include "dsp_afe/bss.h"

#include <math.h>
#include <string.h>

#include "afe_internal.h"
#include "gen_grid.h"

#define ANGLES 181 // every whole degree doa reports, 0 .. 180
#define DEG_PER_HALF_TURN 180.0
#define LOGISTIC_REACH 40.0 // past this the logistic is 0 or 1 in float
#define EXP_SPLIT 8.0       // e^y as (e^(y/8))^8 keeps the series within |x| <= 5
#define GATE_START 0.5f
#define ANGLE_UNKNOWN_DEG (-1)
#define BINS_PER_GAP_CAP 64   // finer bins than this buy nothing at doa's 2 deg grid
#define DENSITY_BINS_CAP 1024 // parts whole degrees past 6 deg off endfire
#define NO_PEAK (-1)

typedef struct {
    size_t window; // updates the ring holds
    size_t first;  // updates ahead of the first search
    uint32_t recheck_hops;
    uint32_t forget;  // updates of speech a seat may go unreached
    uint32_t horizon; // hops a reading stays in the window; 0 keeps it
    uint32_t history; // hops of held angles numbered_back reaches
    size_t n_bins;    // delay bins of the density
    size_t standing;  // most peaks it holds: never two in adjacent bins
    size_t peaks;     // most peaks bins_per_gap apart
} span_t;

typedef struct {
    int16_t *ring;
    uint32_t *stamp;  // hop of each reading in the ring
    int16_t *held;    // held angle of each of the last history + 1 hops
    uint16_t *bin_of; // delay bin of each degree
    uint32_t *counts;
    uint32_t *density;
    uint16_t *standing;
    uint16_t *chosen;
    float *cand_centre;
    uint16_t *cand_bin;
    uint16_t *fresh; // candidates no seat holds
    float *fresh_centre;
    float *prev; // centres of the last recheck's candidates
} views_t;

struct dsp_afe_bss_s {
    dsp_afe_bss_config_t cfg;
    span_t span;
    views_t buf;
    double slope;
    double width;        // samples of delay one bin spans
    double lo;           // delay at the lower edge of bin 0
    double core;         // reach of a peak's mass either side, samples
    float delay[ANGLES]; // samples of delay of each whole degree
    float rise[ANGLES];  // broadside split's share of number 1
    float keep_delay;
    uint8_t side; // keep_deg's broadside number
    float half_gap;
    float share_hi;
    int16_t last_angle;
    size_t head;
    size_t filled;
    size_t n_prev;
    uint32_t hist[ANGLES];
    uint32_t hop;
    uint32_t heard; // updates counted so far: the clock of seat_forget_s
    uint32_t since;
    uint8_t seats;
    uint8_t agree;
    float centres[DSP_AFE_BSS_SEATS_MAX]; // samples of delay, rising
    float offsets[DSP_AFE_BSS_SEATS_MAX - 1];
    uint32_t seen[DSP_AFE_BSS_SEATS_MAX]; // heard at each seat's last reading
    uint32_t votes[DSP_AFE_BSS_SEATS_MAX];
    uint8_t number[DSP_AFE_BSS_SEATS_MAX];
    float gate;
    float shares[DSP_AFE_BSS_SEATS_MAX]; // gate of each number at the last search
};

typedef struct {
    float centre;
    uint8_t number;
    uint32_t seen;
    uint32_t votes;
    int32_t bin; // the seat's peak in the window, or NO_PEAK
} seat_row_t;

static double speech_s(const dsp_afe_bss_config_t *cfg)
{
    return (double)GEN_GRID_HOP_SAMPLES / GEN_GRID_SAMPLE_RATE_HZ * cfg->update_every_hops;
}

static double tau_max_of(const dsp_afe_bss_config_t *cfg)
{
    return (double)cfg->spacing_m * GEN_GRID_SAMPLE_RATE_HZ / (double)cfg->speed_of_sound_m_s;
}

static double density_bins(const dsp_afe_bss_config_t *cfg)
{
    const float delay0 = (float)(tau_max_of(cfg) * afe_cos_series(0.0));
    return floor(2.0 * (double)delay0 / ((double)cfg->min_gap / cfg->bins_per_gap)) + 1.0;
}

static bool config_ok(const dsp_afe_bss_config_t *cfg)
{
    if (cfg == NULL) { return false; }
    const bool ok =
        (cfg->mode == DSP_AFE_BSS_FIXED || cfg->mode == DSP_AFE_BSS_LEARN) &&
        (cfg->keep_rule == DSP_AFE_BSS_KEEP_ANGLE || cfg->keep_rule == DSP_AFE_BSS_KEEP_MOST) &&
        cfg->keep_deg >= 0.0f && cfg->keep_deg <= ANGLES - 1 && cfg->gate_slope > 0.0f &&
        cfg->seats_max >= 2 && cfg->seats_max <= DSP_AFE_BSS_SEATS_MAX && cfg->decision_s > 0.0f &&
        cfg->recheck_s > 0.0f && cfg->first_speech_s > 0.0f && cfg->first_speech_s <= cfg->decision_s &&
        cfg->confirm >= 1 && cfg->occupancy_min >= 0.0f && cfg->occupancy_min < 1.0f && cfg->min_gap > 0.0f &&
        cfg->bins_per_gap >= 1 && cfg->bins_per_gap <= BINS_PER_GAP_CAP &&
        cfg->smooth_bins <= cfg->bins_per_gap && cfg->prominence > 0.0f && cfg->prominence <= 1.0f &&
        cfg->core_share > 0.0f && cfg->core_share <= 0.5f && cfg->seat_follow >= 0.0f &&
        cfg->seat_follow <= 1.0f && cfg->boundary_share_min >= 0.0f && cfg->boundary_share_min <= 0.5f &&
        cfg->boundary_follow >= 0.0f && cfg->boundary_follow <= 1.0f && cfg->seat_forget_s > 0.0f &&
        cfg->window_horizon_s >= 0.0f && cfg->update_every_hops >= 1 && cfg->spacing_m > 0.0f &&
        cfg->speed_of_sound_m_s > 0.0f && cfg->history_s >= 0.0f;
    return ok && density_bins(cfg) <= DENSITY_BINS_CAP;
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
    const size_t n_bins = (size_t)density_bins(cfg);
    span_t s = {
        .window = rounded((double)cfg->decision_s / per_update),
        .first = rounded((double)cfg->first_speech_s / per_update),
        .recheck_hops = (uint32_t)rounded((double)cfg->recheck_s / hop_s),
        .forget = (uint32_t)rounded((double)cfg->seat_forget_s / per_update),
        .horizon = (uint32_t)floor((double)cfg->window_horizon_s / hop_s + 0.5),
        .history = (uint32_t)floor((double)cfg->history_s / hop_s + 0.5),
        .n_bins = n_bins,
        .standing = (n_bins - 1u) / 2u + 1u,
        .peaks = (n_bins - 1u) / cfg->bins_per_gap + 1u,
    };
    if (s.first > s.window) { s.first = s.window; }
    return s;
}

static dsp_afe_bss_t *carve(afe_arena_t *a, const span_t *s)
{
    dsp_afe_bss_t *st = afe_take(a, sizeof(*st));
    int16_t *ring = afe_take(a, s->window * sizeof(int16_t));
    uint32_t *stamp = afe_take(a, s->window * sizeof(uint32_t));
    int16_t *held = afe_take(a, (s->history + 1u) * sizeof(int16_t));
    uint16_t *bin_of = afe_take(a, ANGLES * sizeof(uint16_t));
    uint32_t *counts = afe_take(a, s->n_bins * sizeof(uint32_t));
    uint32_t *density = afe_take(a, s->n_bins * sizeof(uint32_t));
    uint16_t *standing = afe_take(a, s->standing * sizeof(uint16_t));
    uint16_t *chosen = afe_take(a, s->peaks * sizeof(uint16_t));
    float *cand_centre = afe_take(a, s->peaks * sizeof(float));
    uint16_t *cand_bin = afe_take(a, s->peaks * sizeof(uint16_t));
    uint16_t *fresh = afe_take(a, s->peaks * sizeof(uint16_t));
    float *fresh_centre = afe_take(a, s->peaks * sizeof(float));
    float *prev = afe_take(a, s->peaks * sizeof(float));
    if (st != NULL) {
        st->buf = (views_t){ring,   stamp,       held,     bin_of, counts,       density, standing,
                            chosen, cand_centre, cand_bin, fresh,  fresh_centre, prev};
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
    st->heard = 0;
    st->since = 0;
    st->seats = 0;
    st->agree = 0;
    st->n_prev = 0;
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
        st->buf.held[h] = ANGLE_UNKNOWN_DEG;
    }
}

esp_err_t dsp_afe_bss_init(dsp_afe_bss_t **out, const dsp_afe_bss_config_t *cfg, void *mem, size_t bytes)
{
    if (out == NULL || mem == NULL || !config_ok(cfg)) { return ESP_ERR_INVALID_ARG; }
    const span_t s = span_of(cfg);
    afe_arena_t arena = afe_arena(mem, bytes);
    dsp_afe_bss_t *st = carve(&arena, &s);
    if (st == NULL || arena.used > arena.cap) { return ESP_ERR_INVALID_SIZE; }
    const views_t buf = st->buf;
    memset(st, 0, sizeof(*st));
    st->buf = buf;
    st->cfg = *cfg;
    st->span = s;
    st->slope = (double)cfg->gate_slope;
    const double tau_max = tau_max_of(cfg);
    for (size_t d = 0; d < ANGLES; d++) {
        st->delay[d] = (float)(tau_max * afe_cos_series((double)d * AFE_PI / DEG_PER_HALF_TURN));
    }
    st->keep_delay = (float)(tau_max * afe_cos_series((double)cfg->keep_deg * AFE_PI / DEG_PER_HALF_TURN));
    st->side = st->keep_delay > 0.0f ? 1u : 0u;
    const float axis[1] = {0.0f};
    for (size_t d = 0; d < ANGLES; d++) {
        st->rise[d] = above(st, st->delay[d], axis, 1, 1) - above(st, st->delay[d], axis, 1, 2);
    }
    st->half_gap = 0.5f * cfg->min_gap;
    st->share_hi = 1.0f - cfg->boundary_share_min;
    st->width = (double)cfg->min_gap / cfg->bins_per_gap;
    st->lo = -(double)st->delay[0];
    st->core = (double)cfg->min_gap * (double)cfg->core_share;
    const double top = (double)(s.n_bins - 1u);
    for (size_t d = 0; d < ANGLES; d++) {
        const double at = floor(((double)st->delay[d] - st->lo) / st->width);
        st->buf.bin_of[d] = (uint16_t)(at < 0.0 ? 0.0 : at > top ? top : at);
    }
    dsp_afe_bss_reset(st);
    *out = st;
    return ESP_OK;
}

static void count(dsp_afe_bss_t *st, int16_t angle)
{
    if (st->filled == st->span.window) {
        st->hist[st->buf.ring[st->head]]--;
    } else {
        st->filled++;
    }
    st->buf.ring[st->head] = angle;
    st->buf.stamp[st->head] = st->hop;
    st->hist[angle]++;
    st->head = (st->head + 1) % st->span.window;
}

// The oldest reading sits filled places behind head.
static void expire(dsp_afe_bss_t *st)
{
    while (st->filled > 0) {
        const size_t tail = (st->head + st->span.window - st->filled) % st->span.window;
        if (st->hop - st->buf.stamp[tail] <= st->span.horizon) { return; }
        st->hist[st->buf.ring[tail]]--;
        st->filled--;
    }
}

// A reading keeps the seat whose region holds it from being forgotten.
static void mark(dsp_afe_bss_t *st, float delay)
{
    const float *c = st->centres;
    size_t j = 0;
    for (size_t i = 0; i + 1 < st->seats; i++) {
        if (delay > 0.5f * (c[i] + c[i + 1]) + st->offsets[i]) { j = i + 1; }
    }
    st->seen[j] = st->heard;
    st->votes[j]++;
}

// A seat's two boundaries merge into one at the midpoint of its neighbours once it goes.
static void forget(dsp_afe_bss_t *st)
{
    size_t i = 0;
    while (i < st->seats && st->seats > 2) {
        if (st->heard - st->seen[i] < st->span.forget) {
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
}

static void smooth_density(dsp_afe_bss_t *st)
{
    const size_t n = st->span.n_bins;
    const int r = st->cfg.smooth_bins;
    uint32_t *counts = st->buf.counts;
    memset(counts, 0, n * sizeof(uint32_t));
    for (size_t d = 0; d < ANGLES; d++) {
        counts[st->buf.bin_of[d]] += st->hist[d];
    }
    for (size_t i = 0; i < n; i++) {
        uint32_t acc = 0;
        for (int k = -r; k <= r; k++) {
            const long j = (long)i + k;
            if (j >= 0 && j < (long)n) { acc += (uint32_t)(r + 1 - (k < 0 ? -k : k)) * counts[j]; }
        }
        st->buf.density[i] = acc;
    }
}

// Lowest density between bin p and the nearest higher bin one way, 0 when no higher bin lies that way.
static uint32_t col(const uint32_t *s, size_t n, size_t p, int step)
{
    uint32_t low = s[p];
    long i = (long)p;
    while (i + step >= 0 && i + step < (long)n) {
        i += step;
        if (s[i] > s[p]) { return low; }
        if (s[i] < low) { low = s[i]; }
    }
    return 0;
}

// The middle one of the bins tied lowest between bins a and b.
static size_t saddle(const uint32_t *s, size_t a, size_t b)
{
    const size_t from = a < b ? a : b;
    const size_t to = a < b ? b : a;
    uint32_t low = s[from];
    size_t tied = 0;
    for (size_t i = from; i <= to; i++) {
        if (s[i] < low) {
            low = s[i];
            tied = 0;
        }
        tied += s[i] == low ? 1u : 0u;
    }
    size_t seen = 0;
    for (size_t i = from; i <= to; i++) {
        if (s[i] == low && seen++ == tied / 2u) { return i; }
    }
    return from;
}

static size_t gap_of(size_t a, size_t b)
{
    return a > b ? a - b : b - a;
}

// Tallest first, the lower bin first among equals.
static void sort_tallest(uint16_t *bins, size_t n, const uint32_t *s)
{
    for (size_t i = 1; i < n; i++) {
        const uint16_t v = bins[i];
        size_t j = i;
        while (j > 0 && s[bins[j - 1]] < s[v]) {
            bins[j] = bins[j - 1];
            j--;
        }
        bins[j] = v;
    }
}

static void sort_bins(uint16_t *bins, size_t n)
{
    for (size_t i = 1; i < n; i++) {
        const uint16_t v = bins[i];
        size_t j = i;
        while (j > 0 && bins[j - 1] > v) {
            bins[j] = bins[j - 1];
            j--;
        }
        bins[j] = v;
    }
}

// Peaks standing out by prominence, the higher within min_gap, whose core holds occupancy_min of the window.
static size_t find_candidates(dsp_afe_bss_t *st)
{
    const uint32_t *s = st->buf.density;
    const size_t n = st->span.n_bins;
    uint16_t *standing = st->buf.standing;
    size_t n_standing = 0;
    for (size_t p = 0; p < n; p++) {
        if (s[p] == 0 || (p > 0 && s[p] <= s[p - 1]) || (p + 1 < n && s[p] < s[p + 1])) { continue; }
        const uint32_t left = col(s, n, p, -1);
        const uint32_t right = col(s, n, p, 1);
        const uint32_t higher = left > right ? left : right;
        if ((float)(s[p] - higher) >= st->cfg.prominence * (float)s[p]) {
            standing[n_standing++] = (uint16_t)p;
        }
    }
    sort_tallest(standing, n_standing, s);
    uint16_t *chosen = st->buf.chosen;
    size_t n_chosen = 0;
    for (size_t i = 0; i < n_standing; i++) {
        bool far = true;
        for (size_t q = 0; q < n_chosen && far; q++) {
            far = gap_of(standing[i], chosen[q]) >= st->cfg.bins_per_gap;
        }
        if (far) { chosen[n_chosen++] = standing[i]; }
    }
    sort_bins(chosen, n_chosen);
    size_t found = 0;
    for (size_t q = 0; q < n_chosen; q++) {
        const double peak = st->lo + ((double)chosen[q] + 0.5) * st->width;
        uint32_t mass = 0;
        float acc = 0.0f;
        for (int d = ANGLES - 1; d >= 0; d--) {
            if (st->hist[d] > 0 && fabs((double)st->delay[d] - peak) <= st->core) {
                mass += st->hist[d];
                acc = acc + (float)st->hist[d] * st->delay[d];
            }
        }
        if (mass > 0 && !((float)mass < st->cfg.occupancy_min * (float)st->filled)) {
            st->buf.cand_centre[found] = acc / (float)mass;
            st->buf.cand_bin[found] = chosen[q];
            found++;
        }
    }
    return found;
}

// Offset off the midpoint of seats j and j + 1 of the density's lowest point between their peaks a and b.
static float target(const dsp_afe_bss_t *st, size_t j, size_t a, size_t b)
{
    const float *c = st->centres;
    float cut = (float)(st->lo + ((double)saddle(st->buf.density, a, b) + 0.5) * st->width);
    const float gap = c[j + 1] - c[j];
    const float low = c[j] + st->cfg.boundary_share_min * gap;
    const float high = c[j] + st->share_hi * gap;
    cut = cut < low ? low : cut;
    cut = cut > high ? high : cut;
    return cut - 0.5f * (c[j] + c[j + 1]);
}

static void space(dsp_afe_bss_t *st)
{
    float *c = st->centres;
    for (size_t i = 0; i + 1 < st->seats; i++) {
        if (c[i + 1] - c[i] < st->cfg.min_gap) {
            const float mid = 0.5f * (c[i] + c[i + 1]);
            c[i] = mid - st->half_gap;
            c[i + 1] = mid + st->half_gap;
        }
    }
}

// Whether fresh centres repeat the last recheck's within half min_gap, one for one when paired.
static bool agrees(const dsp_afe_bss_t *st, const float *fresh, size_t n, bool paired)
{
    if (n == 0 || st->n_prev == 0) { return false; }
    if (paired) {
        if (n != st->n_prev) { return false; }
        for (size_t i = 0; i < n; i++) {
            if (!(fabsf(fresh[i] - st->buf.prev[i]) < st->half_gap)) { return false; }
        }
        return true;
    }
    for (size_t i = 0; i < n; i++) {
        bool near = false;
        for (size_t q = 0; q < st->n_prev && !near; q++) {
            near = fabsf(fresh[i] - st->buf.prev[q]) < st->half_gap;
        }
        if (!near) { return false; }
    }
    return true;
}

static void remember(dsp_afe_bss_t *st, const float *fresh, size_t n, bool agreed)
{
    st->agree = agreed ? (uint8_t)(st->agree + 1u) : 1u;
    memcpy(st->buf.prev, fresh, n * sizeof(float));
    st->n_prev = n;
}

static void drop_candidates(dsp_afe_bss_t *st)
{
    st->agree = 0;
    st->n_prev = 0;
}

static void window_votes(dsp_afe_bss_t *st)
{
    memset(st->votes, 0, sizeof(st->votes));
    for (int d = ANGLES - 1; d >= 0; d--) {
        if (st->hist[d] > 0) { st->votes[nearest(st->delay[d], st->centres, st->seats)] += st->hist[d]; }
    }
}

static bool picked(const size_t *order, size_t n, size_t i)
{
    for (size_t k = 0; k < n; k++) {
        if (order[k] == i) { return true; }
    }
    return false;
}

// The tallest candidate the first k of order leave, the first one among equals.
static size_t tallest_left(const dsp_afe_bss_t *st, size_t found, const size_t *order, size_t k)
{
    const uint32_t *s = st->buf.density;
    const uint16_t *bins = st->buf.cand_bin;
    size_t best = found;
    for (size_t i = 0; i < found; i++) {
        if (!picked(order, k, i) && (best == found || s[bins[i]] > s[bins[best]])) { best = i; }
    }
    return best;
}

// More candidates than seats: only once those left out stand below (1 - prominence) of the lowest kept.
static void first_split(dsp_afe_bss_t *st, size_t found)
{
    if (found < 2) {
        drop_candidates(st);
        return;
    }
    float *c = st->buf.cand_centre;
    uint16_t *bins = st->buf.cand_bin;
    const size_t seats_max = st->cfg.seats_max;
    if (found > seats_max) {
        size_t order[DSP_AFE_BSS_SEATS_MAX + 1];
        for (size_t k = 0; k <= seats_max; k++) {
            order[k] = tallest_left(st, found, order, k);
        }
        const float lowest = (float)st->buf.density[bins[order[seats_max - 1u]]];
        const float next = (float)st->buf.density[bins[order[seats_max]]];
        if (next > (1.0f - st->cfg.prominence) * lowest) {
            drop_candidates(st);
            return;
        }
        size_t kept = 0;
        for (size_t i = 0; i < found; i++) {
            if (picked(order, seats_max, i)) {
                c[kept] = c[i];
                bins[kept] = bins[i];
                kept++;
            }
        }
        found = kept;
    }
    remember(st, c, found, agrees(st, c, found, true));
    if (st->agree < st->cfg.confirm) { return; }
    st->seats = (uint8_t)found;
    for (size_t j = 0; j < found; j++) {
        st->centres[j] = c[j];
        st->number[j] = (uint8_t)j;
        st->seen[j] = st->heard;
    }
    window_votes(st);
    for (size_t j = 0; j + 1 < found; j++) {
        st->offsets[j] = target(st, j, bins[j], bins[j + 1]);
    }
    drop_candidates(st);
}

static seat_row_t held_row(const dsp_afe_bss_t *st, size_t j, const int32_t *bins)
{
    return (seat_row_t){st->centres[j], st->number[j], st->seen[j], st->votes[j], bins[j]};
}

static void sort_rows(seat_row_t *rows, size_t n)
{
    for (size_t i = 1; i < n; i++) {
        const seat_row_t row = rows[i];
        size_t k = i;
        while (k > 0 && rows[k - 1].centre > row.centre) {
            rows[k] = rows[k - 1];
            k--;
        }
        rows[k] = row;
    }
}

static void set_rows(dsp_afe_bss_t *st, const seat_row_t *rows, size_t n)
{
    st->seats = (uint8_t)n;
    for (size_t j = 0; j < n; j++) {
        st->centres[j] = rows[j].centre;
        st->number[j] = rows[j].number;
        st->seen[j] = rows[j].seen;
        st->votes[j] = rows[j].votes;
    }
}

// A boundary between two seats with peaks in the window sits at its low point; any other at the midpoint.
static void row_offsets(dsp_afe_bss_t *st, const seat_row_t *rows)
{
    for (size_t j = 0; j + 1 < st->seats; j++) {
        const bool both = rows[j].bin != NO_PEAK && rows[j + 1].bin != NO_PEAK;
        st->offsets[j] = both ? target(st, j, (size_t)rows[j].bin, (size_t)rows[j + 1].bin) : 0.0f;
    }
}

static bool number_held(const dsp_afe_bss_t *st, uint8_t number)
{
    for (size_t j = 0; j < st->seats; j++) {
        if (st->number[j] == number) { return true; }
    }
    return false;
}

// Fresh seats take the smallest free numbers; held ones keep centre, number and counts.
static void take(dsp_afe_bss_t *st, size_t n_new, const int32_t *bins)
{
    seat_row_t rows[DSP_AFE_BSS_SEATS_MAX];
    size_t n = 0;
    for (size_t j = 0; j < st->seats; j++) {
        rows[n++] = held_row(st, j, bins);
    }
    uint8_t number = 0;
    for (size_t k = 0; k < n_new; k++) {
        while (number_held(st, number)) {
            number++;
        }
        rows[n++] =
            (seat_row_t){st->buf.fresh_centre[k], number, st->heard, 0, st->buf.cand_bin[st->buf.fresh[k]]};
        number++;
    }
    sort_rows(rows, n);
    set_rows(st, rows, n);
    row_offsets(st, rows);
}

// Every seat taken: a seat no candidate stands near moves to the nearest fresh one, keeping its number.
static void move_vacant(dsp_afe_bss_t *st, size_t n_fresh, const int32_t *match_bins, const bool *vacant_seat)
{
    int32_t bins[DSP_AFE_BSS_SEATS_MAX];
    bool vacant[DSP_AFE_BSS_SEATS_MAX];
    memcpy(bins, match_bins, sizeof(bins));
    memcpy(vacant, vacant_seat, sizeof(vacant));
    for (size_t k = 0; k < n_fresh; k++) {
        const float c = st->buf.fresh_centre[k];
        size_t at = st->seats;
        float best = 0.0f;
        for (size_t j = 0; j < st->seats; j++) {
            const float d = fabsf(c - st->centres[j]);
            if (vacant[j] && (at == st->seats || d < best)) {
                best = d;
                at = j;
            }
        }
        if (at == st->seats) { break; }
        vacant[at] = false;
        st->centres[at] = c;
        st->seen[at] = st->heard;
        bins[at] = st->buf.cand_bin[st->buf.fresh[k]];
    }
    seat_row_t rows[DSP_AFE_BSS_SEATS_MAX];
    for (size_t j = 0; j < st->seats; j++) {
        rows[j] = held_row(st, j, bins);
    }
    sort_rows(rows, st->seats);
    set_rows(st, rows, st->seats);
    space(st);
    row_offsets(st, rows);
}

// Seats grow or move once confirm rechecks agree, and never shrink here: a seat goes only by forget.
static void cluster(dsp_afe_bss_t *st)
{
    smooth_density(st);
    const size_t found = find_candidates(st);
    if (st->seats < 2) {
        first_split(st, found);
        return;
    }
    const float *cand = st->buf.cand_centre;
    float held[DSP_AFE_BSS_SEATS_MAX];
    int32_t match[DSP_AFE_BSS_SEATS_MAX];
    memcpy(held, st->centres, sizeof(held));
    for (size_t j = 0; j < DSP_AFE_BSS_SEATS_MAX; j++) {
        match[j] = NO_PEAK;
    }
    size_t n_fresh = 0;
    for (size_t ci = 0; ci < found; ci++) {
        const size_t j = nearest(cand[ci], held, st->seats);
        const float dist = fabsf(cand[ci] - held[j]);
        if (!(dist < st->cfg.min_gap)) {
            st->buf.fresh[n_fresh] = (uint16_t)ci;
            st->buf.fresh_centre[n_fresh] = cand[ci];
            n_fresh++;
        } else if (match[j] == NO_PEAK || dist < fabsf(cand[match[j]] - held[j])) {
            match[j] = (int32_t)ci;
        }
    }
    for (size_t j = 0; j < st->seats; j++) {
        if (match[j] != NO_PEAK) {
            st->centres[j] = st->centres[j] + st->cfg.seat_follow * (cand[match[j]] - st->centres[j]);
        }
    }
    space(st);
    const size_t room = st->cfg.seats_max - st->seats;
    bool vacant[DSP_AFE_BSS_SEATS_MAX];
    int32_t bins[DSP_AFE_BSS_SEATS_MAX];
    bool any_vacant = false;
    for (size_t j = 0; j < DSP_AFE_BSS_SEATS_MAX; j++) {
        vacant[j] = j < st->seats && match[j] == NO_PEAK;
        any_vacant = any_vacant || vacant[j];
        bins[j] = match[j] == NO_PEAK ? NO_PEAK : (int32_t)st->buf.cand_bin[match[j]];
    }
    if (n_fresh > 0 && (room > 0 || any_vacant)) {
        remember(st, st->buf.fresh_centre, n_fresh, agrees(st, st->buf.fresh_centre, n_fresh, false));
        if (st->agree >= st->cfg.confirm) {
            if (room > 0) {
                take(st, n_fresh < room ? n_fresh : room, bins);
            } else {
                move_vacant(st, n_fresh, bins, vacant);
            }
            drop_candidates(st);
        }
        return;
    }
    drop_candidates(st);
    for (size_t j = 0; j + 1 < st->seats; j++) {
        if (match[j] != NO_PEAK && match[j + 1] != NO_PEAK) {
            const float t = target(st, j, st->buf.cand_bin[match[j]], st->buf.cand_bin[match[j + 1]]);
            st->offsets[j] = st->offsets[j] + st->cfg.boundary_follow * (t - st->offsets[j]);
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
        if (st->span.horizon > 0) { expire(st); }
        if (counted) {
            st->heard++;
            count(st, angle_deg);
            if (st->seats >= 2) { mark(st, st->delay[angle_deg]); }
            forget(st);
        }
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
    st->buf.held[st->hop % (st->span.history + 1u)] = st->last_angle;
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
    const int16_t angle = st->hop > back_hops
                              ? st->buf.held[(st->hop - 1u - back_hops) % (st->span.history + 1u)]
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
