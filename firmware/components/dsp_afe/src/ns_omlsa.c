#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "dsp_afe/ns.h"
#include "gen_afe.h"
#include "gen_grid.h"

#define N_BINS GEN_GRID_N_BINS
#define N_WINDOWS GEN_AFE_NS_MIN_SUBWINDOWS
#define E1_POINTS GEN_AFE_NS_E1_POINTS
#define FREQ_TAPS (2 * GEN_AFE_NS_FREQ_SMOOTH_BINS + 1)
#define LOCAL_TAPS (2 * GEN_AFE_NS_XI_LOCAL_BINS + 1)
#define GLOBAL_TAPS (2 * GEN_AFE_NS_XI_GLOBAL_BINS + 1)
#define LOG2_TERMS 5
#define EXP2_DEGREE 7
#define E1_SERIES_TERMS 40
#define EXP_SERIES_TERMS 40
#define COS_SERIES_TERMS 30
#define EULER_GAMMA 0.57721566490153286
#define LN_2 0.69314718055994530942
#define LN_10 2.30258509299404568402
#define LOG2_10 3.32192809488736234787
#define PI 3.14159265358979323846
#define EXP2_MIN (-126.0f)
#define EXP2_MAX 127.0f
#define EMPTY_XI_DB (-100.0f) // omlsa.m's level of a frame without xi

static const float kFrameBandHz[2] = GEN_AFE_NS_FRAME_BAND_HZ;
static const float kLocalMeanHz[2] = GEN_AFE_NS_LOCAL_MEAN_HZ;
static const float kLocalResetHz[2] = GEN_AFE_NS_LOCAL_RESET_HZ;
static const float kXiLocalDb[2] = GEN_AFE_NS_XI_LOCAL_DB;
static const float kXiGlobalDb[2] = GEN_AFE_NS_XI_GLOBAL_DB;
static const float kXiFrameDb[2] = GEN_AFE_NS_XI_FRAME_DB;
static const float kXiPeakDb[2] = GEN_AFE_NS_XI_PEAK_DB;

typedef struct {
    size_t first;
    size_t last; // inclusive
} bin_range_t;

typedef struct {
    bool started;
    uint32_t hop;
    uint32_t subwindow_hops;
    uint32_t in_subwindow;
    uint32_t window_head;
    bin_range_t frame_bins;
    bin_range_t mean_bins;
    bin_range_t reset_bins;
    float floor_db;
    float log2_gain_min;
    float alpha_s;
    float alpha_d;
    float alpha_xi;
    float alpha_eta;
    float eta_min;
    float power_floor;
    float p_min;
    float table_per_v;
    float absent_power;  // gamma0 * B_min
    float absent_smooth; // zeta0 * B_min
    float xi_frame;
    float xi_peak_db;
    float sqrt_half;
    float db_per_log2;
    float log2_e;
    float log2_coeffs[LOG2_TERMS];
    float exp2_coeffs[EXP2_DEGREE + 1];
    float freq_taps[FREQ_TAPS];
    float local_taps[LOCAL_TAPS];
    float global_taps[GLOBAL_TAPS];
    float table[E1_POINTS];
    float lambda_d[N_BINS];
    float lambda_dav[N_BINS];
    float eta_2term[N_BINS];
    float xi[N_BINS];
    float s[N_BINS];
    float st[N_BINS];
    float s_min[N_BINS];
    float s_min_t[N_BINS];
    float s_act[N_BINS];
    float s_act_t[N_BINS];
    float windows[N_WINDOWS][N_BINS];
    float windows_t[N_WINDOWS][N_BINS];
    float eta[N_BINS];
    float v[N_BINS];
    float absent[N_BINS];
    float p_local[N_BINS];
    float p_global[N_BINS];
} omlsa_state_t;

static bool config_ok(const dsp_afe_ns_omlsa_config_t *cfg)
{
    return cfg != NULL && cfg->floor_db <= 0.0f;
}

static double exp_series(double x)
{
    double term = 1.0;
    double total = 1.0;
    for (int k = 1; k <= EXP_SERIES_TERMS; k++) {
        term = term * x / k;
        total = total + term;
    }
    return total;
}

static double e1_smooth(double v)
{
    double term = 1.0;
    double total = -EULER_GAMMA;
    for (int k = 1; k <= E1_SERIES_TERMS; k++) {
        term = term * -v / k;
        total = total - term / k;
    }
    return total;
}

static double cos_series(double x)
{
    double term = 1.0;
    double total = 1.0;
    for (int k = 1; k <= COS_SERIES_TERMS; k++) {
        term = term * -x * x / ((2 * k - 1) * (2 * k));
        total = total + term;
    }
    return total;
}

// MATLAB's hanning(n) without its zero ends, scaled to sum to one.
static void hann_taps(float *taps, int n)
{
    double raw[GLOBAL_TAPS];
    double total = 0.0;
    for (int k = 1; k <= n; k++) {
        raw[k - 1] = 0.5 * (1.0 - cos_series(2.0 * PI * k / (n + 1)));
        total = total + raw[k - 1];
    }
    for (int k = 0; k < n; k++) {
        taps[k] = (float)(raw[k] / total);
    }
}

static float smoothing(double tau_s)
{
    const double hop_s = (double)GEN_GRID_HOP_SAMPLES / GEN_GRID_SAMPLE_RATE_HZ;
    return (float)exp_series(-hop_s / tau_s);
}

static size_t bin_of(float freq_hz)
{
    const long bin = lrint((double)freq_hz / GEN_GRID_SAMPLE_RATE_HZ * GEN_GRID_FFT_SIZE);
    return bin < N_BINS - 1 ? (size_t)bin : N_BINS - 1;
}

static bin_range_t bins_of(const float band_hz[2])
{
    return (bin_range_t){.first = bin_of(band_hz[0]), .last = bin_of(band_hz[1])};
}

static void set_floor(omlsa_state_t *st, float floor_db)
{
    st->floor_db = floor_db;
    st->log2_gain_min = (float)((double)floor_db / 20.0 * LOG2_10);
}

static float log2_f32(const omlsa_state_t *st, float x)
{
    int exponent = 0;
    float mantissa = frexpf(x, &exponent);
    if (mantissa < st->sqrt_half) {
        mantissa = mantissa * 2.0f;
        exponent = exponent - 1;
    }
    const float t = (mantissa - 1.0f) / (mantissa + 1.0f);
    const float t2 = t * t;
    const float *c = st->log2_coeffs;
    const float series = t * (c[0] + t2 * (c[1] + t2 * (c[2] + t2 * (c[3] + t2 * c[4]))));
    return (float)exponent + series;
}

static float exp2_f32(const omlsa_state_t *st, float y)
{
    if (y < EXP2_MIN) { return 0.0f; }
    const float whole = floorf(y + 0.5f);
    const float f = y - whole;
    float p = st->exp2_coeffs[EXP2_DEGREE];
    for (int k = EXP2_DEGREE - 1; k >= 0; k--) {
        p = st->exp2_coeffs[k] + f * p;
    }
    return ldexpf(p, (int)(whole > EXP2_MAX ? EXP2_MAX : whole));
}

static float exp_f32(const omlsa_state_t *st, float x)
{
    return exp2_f32(st, x * st->log2_e);
}

static float max_f(float a, float b)
{
    return a > b ? a : b;
}

static float min_f(float a, float b)
{
    return a < b ? a : b;
}

// out[k] = sum over i of taps[i] x[k + w - i], i rising, zero outside the bins.
static float smooth_at(const float *x, size_t k, const float *taps, int n_taps)
{
    const int w = (n_taps - 1) / 2;
    float acc = 0.0f;
    for (int i = 0; i < n_taps; i++) {
        const int j = (int)k + w - i;
        acc += taps[i] * (j >= 0 && j < N_BINS ? x[j] : 0.0f);
    }
    return acc;
}

static float ordered_mean(const float *x, bin_range_t bins)
{
    float acc = 0.0f;
    for (size_t k = bins.first; k <= bins.last; k++) {
        acc += x[k];
    }
    return acc / (float)(bins.last - bins.first + 1);
}

static float ramp(float level_db, const float bounds_db[2], float p_min)
{
    if (level_db <= bounds_db[0]) { return p_min; }
    if (level_db >= bounds_db[1]) { return 1.0f; }
    return p_min + (level_db - bounds_db[0]) / (bounds_db[1] - bounds_db[0]) * (1.0f - p_min);
}

static float presence(const omlsa_state_t *st, float q, float eta, float v)
{
    return 1.0f / (1.0f + q / (1.0f - q) * (1.0f + eta) * exp_f32(st, -v));
}

static void prior_snr(const omlsa_state_t *st, float power, float noise, float eta_2term, float *gamma,
                      float *eta, float *v)
{
    *gamma = power / max_f(noise, st->power_floor);
    const float fresh = st->alpha_eta * eta_2term + (1.0f - st->alpha_eta) * max_f(*gamma - 1.0f, 0.0f);
    *eta = max_f(fresh, st->eta_min);
    *v = *gamma * *eta / (1.0f + *eta);
}

static void reset_state(omlsa_state_t *st)
{
    st->started = false;
    st->hop = 0;
    st->in_subwindow = 0;
    st->window_head = 0;
    st->xi_frame = 0.0f;
    st->xi_peak_db = kXiPeakDb[0];
    memset(st->lambda_d, 0, sizeof(st->lambda_d));
    memset(st->lambda_dav, 0, sizeof(st->lambda_dav));
    for (size_t k = 0; k < N_BINS; k++) {
        st->eta_2term[k] = 1.0f;
    }
    memset(st->xi, 0, sizeof(st->xi));
}

static void track_noise(omlsa_state_t *st, const float *power)
{
    const float a_s = st->alpha_s;
    const float bias = GEN_AFE_NS_MIN_BIAS;
    const bool warming = st->hop < st->subwindow_hops - 1;
    for (size_t k = 0; k < N_BINS; k++) {
        const float sf = smooth_at(power, k, st->freq_taps, FREQ_TAPS);
        if (st->hop == 0) {
            st->s[k] = sf;
            st->st[k] = sf;
            st->lambda_dav[k] = power[k];
        } else {
            st->s[k] = a_s * st->s[k] + (1.0f - a_s) * sf;
        }
        st->s_min[k] = warming ? st->s[k] : min_f(st->s_min[k], st->s[k]);
        st->s_act[k] = warming ? st->s[k] : min_f(st->s_act[k], st->s[k]);
        const bool absent =
            power[k] < st->absent_power * st->s_min[k] && st->s[k] < st->absent_smooth * st->s_min[k];
        st->absent[k] = absent ? 1.0f : 0.0f;
    }
    for (size_t k = 0; k < N_BINS; k++) {
        const float weight = smooth_at(st->absent, k, st->freq_taps, FREQ_TAPS);
        float weighted = 0.0f;
        for (int i = 0; i < FREQ_TAPS; i++) {
            const int j = (int)k + (FREQ_TAPS - 1) / 2 - i;
            weighted += st->freq_taps[i] * (j >= 0 && j < N_BINS ? st->absent[j] * power[j] : 0.0f);
        }
        const float sft = weight != 0.0f ? weighted / weight : st->st[k];
        if (warming) {
            st->st[k] = st->s[k];
            st->s_min_t[k] = st->st[k];
            st->s_act_t[k] = st->st[k];
        } else {
            st->st[k] = a_s * st->st[k] + (1.0f - a_s) * sft;
            st->s_min_t[k] = min_f(st->s_min_t[k], st->st[k]);
            st->s_act_t[k] = min_f(st->s_act_t[k], st->st[k]);
        }
        const float floor_t = max_f(st->s_min_t[k], st->power_floor);
        const float gamma_min = power[k] / bias / floor_t;
        const float zeta = st->s[k] / bias / floor_t;
        const float gamma1 = GEN_AFE_NS_GAMMA1;
        const float zeta0 = GEN_AFE_NS_ZETA0;
        float p_hat = 0.0f;
        if (gamma_min >= gamma1 || zeta >= zeta0) {
            p_hat = 1.0f;
        } else if (gamma_min > 1.0f) {
            p_hat = presence(st, (gamma1 - gamma_min) / (gamma1 - 1.0f), st->eta[k], st->v[k]);
        }
        const float alpha = st->alpha_d + (1.0f - st->alpha_d) * p_hat;
        st->lambda_dav[k] = alpha * st->lambda_dav[k] + (1.0f - alpha) * power[k];
    }
    if (++st->in_subwindow == st->subwindow_hops) {
        st->in_subwindow = 0;
        if (st->hop == st->subwindow_hops - 1) {
            for (size_t w = 0; w < N_WINDOWS; w++) {
                memcpy(st->windows[w], st->s, sizeof(st->s));
                memcpy(st->windows_t[w], st->st, sizeof(st->st));
            }
        } else {
            memcpy(st->windows[st->window_head], st->s_act, sizeof(st->s_act));
            memcpy(st->windows_t[st->window_head], st->s_act_t, sizeof(st->s_act_t));
            st->window_head = (st->window_head + 1) % N_WINDOWS;
            for (size_t k = 0; k < N_BINS; k++) {
                float lowest = st->windows[0][k];
                float lowest_t = st->windows_t[0][k];
                for (size_t w = 1; w < N_WINDOWS; w++) {
                    lowest = min_f(lowest, st->windows[w][k]);
                    lowest_t = min_f(lowest_t, st->windows_t[w][k]);
                }
                st->s_min[k] = lowest;
                st->s_min_t[k] = lowest_t;
            }
            memcpy(st->s_act, st->s, sizeof(st->s));
            memcpy(st->s_act_t, st->st, sizeof(st->st));
        }
    }
    for (size_t k = 0; k < N_BINS; k++) {
        st->lambda_d[k] = GEN_AFE_NS_NOISE_BIAS * st->lambda_dav[k];
    }
}

static float frame_presence(omlsa_state_t *st)
{
    const float previous = st->xi_frame;
    st->xi_frame = ordered_mean(st->xi, st->frame_bins);
    const bool rising = st->xi_frame - previous >= 0.0f;
    const float frame_db = st->xi_frame > 0.0f ? st->db_per_log2 * log2_f32(st, st->xi_frame) : EMPTY_XI_DB;
    const float low = kXiFrameDb[0];
    const float high = kXiFrameDb[1];
    if (frame_db <= low) { return st->p_min; }
    if (rising) {
        st->xi_peak_db = min_f(max_f(frame_db, kXiPeakDb[0]), kXiPeakDb[1]);
        return 1.0f;
    }
    if (frame_db >= st->xi_peak_db + high) { return 1.0f; }
    if (frame_db <= st->xi_peak_db + low) { return st->p_min; }
    return st->p_min + (frame_db - st->xi_peak_db - low) / (high - low) * (1.0f - st->p_min);
}

// q, the prior probability that speech is absent, is 1 - p_global p_local p_frame; returns p_frame.
static float absence_prior(omlsa_state_t *st)
{
    for (size_t k = 0; k < N_BINS; k++) {
        st->xi[k] = st->alpha_xi * st->xi[k] + (1.0f - st->alpha_xi) * st->eta[k];
    }
    for (size_t k = 0; k < N_BINS; k++) {
        const float local_db =
            st->db_per_log2 * log2_f32(st, smooth_at(st->xi, k, st->local_taps, LOCAL_TAPS));
        const float global_db =
            st->db_per_log2 * log2_f32(st, smooth_at(st->xi, k, st->global_taps, GLOBAL_TAPS));
        st->p_local[k] = ramp(local_db, kXiLocalDb, st->p_min);
        st->p_global[k] = ramp(global_db, kXiGlobalDb, st->p_min);
    }
    const float p_frame = frame_presence(st);
    if (ordered_mean(st->p_local, st->mean_bins) < GEN_AFE_NS_LOCAL_RESET_BELOW) {
        for (size_t k = st->reset_bins.first; k <= st->reset_bins.last; k++) {
            st->p_local[k] = st->p_min;
        }
    }
    return p_frame;
}

static float lsa_gain(const omlsa_state_t *st, float eta, float v)
{
    if (!(v > 0.0f)) { return 1.0f; }
    const float wiener = eta / (1.0f + eta);
    if (v > GEN_AFE_NS_LSA_V_MAX) { return wiener; }
    const float position = v * st->table_per_v;
    int index = (int)position;
    index = index < E1_POINTS - 2 ? index : E1_POINTS - 2;
    const float frac = position - (float)index;
    const float smooth_part = st->table[index] + frac * (st->table[index + 1] - st->table[index]);
    return wiener * (smooth_part / sqrtf(v));
}

static size_t state_bytes(void *ctx)
{
    return config_ok(ctx) ? sizeof(omlsa_state_t) : 0;
}

static esp_err_t init(void *ctx, void *state, size_t bytes)
{
    const dsp_afe_ns_omlsa_config_t *cfg = ctx;
    if (!config_ok(cfg) || state == NULL) { return ESP_ERR_INVALID_ARG; }
    if (bytes < sizeof(omlsa_state_t)) { return ESP_ERR_INVALID_SIZE; }
    omlsa_state_t *st = state;
    const double hop_s = (double)GEN_GRID_HOP_SAMPLES / GEN_GRID_SAMPLE_RATE_HZ;
    st->subwindow_hops = (uint32_t)lrint((double)GEN_AFE_NS_MIN_SUBWINDOW_S / hop_s);
    if (st->subwindow_hops < 2) { return ESP_ERR_INVALID_ARG; }
    st->alpha_s = smoothing(GEN_AFE_NS_SMOOTH_TAU_S);
    st->alpha_d = smoothing(GEN_AFE_NS_NOISE_TAU_S);
    st->alpha_xi = smoothing(GEN_AFE_NS_XI_TAU_S);
    st->alpha_eta = smoothing(GEN_AFE_NS_ETA_TAU_S);
    st->eta_min = (float)exp_series((double)GEN_AFE_NS_ETA_MIN_DB / 10.0 * LN_10);
    st->power_floor = GEN_AFE_NS_POWER_FLOOR;
    st->p_min = GEN_AFE_NS_P_MIN;
    st->table_per_v = (float)((E1_POINTS - 1) / (double)GEN_AFE_NS_LSA_V_MAX);
    st->absent_power = GEN_AFE_NS_GAMMA0 * GEN_AFE_NS_MIN_BIAS;
    st->absent_smooth = GEN_AFE_NS_ZETA0 * GEN_AFE_NS_MIN_BIAS;
    st->frame_bins = bins_of(kFrameBandHz);
    st->mean_bins = bins_of(kLocalMeanHz);
    st->reset_bins = bins_of(kLocalResetHz);
    st->sqrt_half = (float)sqrt(0.5);
    st->db_per_log2 = (float)(10.0 / LOG2_10);
    st->log2_e = (float)(1.0 / LN_2);
    const int odd[LOG2_TERMS] = {1, 3, 5, 7, 9};
    for (int i = 0; i < LOG2_TERMS; i++) {
        st->log2_coeffs[i] = (float)(2.0 / (odd[i] * LN_2));
    }
    double c = 1.0;
    for (int k = 0; k <= EXP2_DEGREE; k++) {
        st->exp2_coeffs[k] = (float)c;
        c = c * LN_2 / (k + 1);
    }
    hann_taps(st->freq_taps, FREQ_TAPS);
    hann_taps(st->local_taps, LOCAL_TAPS);
    hann_taps(st->global_taps, GLOBAL_TAPS);
    const double step = (double)GEN_AFE_NS_LSA_V_MAX / (E1_POINTS - 1);
    for (int i = 0; i < E1_POINTS; i++) {
        st->table[i] = (float)exp_series(0.5 * e1_smooth(i * step));
    }
    set_floor(st, cfg->floor_db);
    reset_state(st);
    return ESP_OK;
}

static esp_err_t process(void *ctx, void *state, const float *power, const float *echo_power, float *gain,
                         float *speech_prob)
{
    const dsp_afe_ns_omlsa_config_t *cfg = ctx;
    omlsa_state_t *st = state;
    if (!config_ok(cfg) || st == NULL || power == NULL || gain == NULL || speech_prob == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (cfg->floor_db != st->floor_db) { set_floor(st, cfg->floor_db); }
    if (!st->started) {
        bool any = false;
        for (size_t k = 0; k < N_BINS; k++) {
            any = any || power[k] > 0.0f;
        }
        if (!any) {
            for (size_t k = 0; k < N_BINS; k++) {
                gain[k] = 1.0f;
            }
            *speech_prob = 0.0f;
            return ESP_OK;
        }
        st->started = true;
        memcpy(st->lambda_d, power, sizeof(st->lambda_d));
    }
    for (size_t k = 0; k < N_BINS; k++) {
        const float noise = echo_power != NULL ? st->lambda_d[k] + echo_power[k] : st->lambda_d[k];
        float gamma = 0.0f;
        prior_snr(st, power[k], noise, st->eta_2term[k], &gamma, &st->eta[k], &st->v[k]);
    }
    track_noise(st, power);
    const float p_frame = absence_prior(st);
    float presence_sum = 0.0f;
    for (size_t k = 0; k < N_BINS; k++) {
        const float noise = echo_power != NULL ? st->lambda_d[k] + echo_power[k] : st->lambda_d[k];
        float gamma = 0.0f;
        float eta = 0.0f;
        float v = 0.0f;
        prior_snr(st, power[k], noise, st->eta_2term[k], &gamma, &eta, &v);
        const float q = min_f(1.0f - st->p_global[k] * st->p_local[k] * p_frame, GEN_AFE_NS_Q_MAX);
        const float p = q < GEN_AFE_NS_Q_PRESENCE_MAX ? presence(st, q, eta, v) : 0.0f;
        const float g_h1 = lsa_gain(st, eta, v);
        const float g = exp2_f32(st, p * log2_f32(st, g_h1) + (1.0f - p) * st->log2_gain_min);
        // LSA lifts bins far under the noise to their expected level; the slot promises 0 .. 1 (KEHOACH 3.9).
        gain[k] = min_f(g, 1.0f);
        st->eta_2term[k] = g_h1 * (g_h1 * gamma);
        if (k >= st->frame_bins.first && k <= st->frame_bins.last) { presence_sum += p; }
    }
    *speech_prob = presence_sum / (float)(st->frame_bins.last - st->frame_bins.first + 1);
    st->hop++;
    return ESP_OK;
}

static const dsp_afe_ns_ops_t s_ops = {
    .state_bytes = state_bytes,
    .init = init,
    .process = process,
};

const dsp_afe_ns_ops_t *dsp_afe_ns_omlsa_ops(void)
{
    return &s_ops;
}
