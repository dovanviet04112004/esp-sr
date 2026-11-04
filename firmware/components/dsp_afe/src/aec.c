#include "dsp_afe/aec.h"

#include <math.h>
#include <string.h>

#include "afe_internal.h"
#include "gen_afe.h"
#include "gen_array.h"
#include "gen_grid.h"

#define HOP GEN_GRID_HOP_SAMPLES
#define N_FFT GEN_GRID_FFT_SIZE
#define N_BINS GEN_GRID_N_BINS
#define PARTITIONS_MAX GEN_AFE_AEC_PARTITIONS
#define DELAY_SAMPLES_MAX (GEN_AFE_AEC_BULK_DELAY_MAX + HOP)
#define PROP_SUM 0.99f
#define ADAPTED_SHARE 0.03f
#define RATE_START 0.25f
#define ECHO_CAP 0.5f
#define ECHO_MIX 0.7f
#define RER_MIX 0.3f
#define SIZE_SHARE 0.1f
#define LEAK_SCALE 2.0f
#define DB_PER_DECADE 10.0f
#define POWER_EPSILON 1e-12f

_Static_assert(N_FFT == 2 * HOP, "overlap-save takes two hops per transform");

typedef struct {
    dsp_spec_cplx_t weights[PARTITIONS_MAX][N_BINS];
    float prop[PARTITIONS_MAX];
    float err_smooth[N_BINS];
    float echo_smooth[N_BINS];
    float echo_tail[HOP];
    float cross;
    float autocorr;
    float leak;
    float sum_adapt;
    float mic_power;
    float err_power;
    uint32_t over;
    bool adapted;
} canceller_t;

struct dsp_afe_aec_s {
    dsp_spec_fft_t *fft;
    uint8_t n_mics;
    uint8_t n_partitions;
    uint8_t head;
    uint32_t bulk_samples;
    uint32_t blocks;
    uint32_t silent_blocks;
    dsp_afe_aec_stats_t stats;
    dsp_spec_cplx_t spectra[PARTITIONS_MAX][N_BINS];
    float previous[HOP];
    float delay[DELAY_SAMPLES_MAX];
    float far[HOP];
    float power[N_BINS];
    float window[N_FFT];
    float block[N_FFT];
    float err[HOP];
    float echo[HOP];
    float rate[N_BINS];
    float err_bin_power[N_BINS];
    float echo_bin_power[N_BINS];
    dsp_spec_cplx_t echo_bins[N_BINS];
    dsp_spec_cplx_t err_bins[N_BINS];
    canceller_t mics[];
};

typedef struct {
    float far;
    float echo;
    float err;
    float cross;
} energies_t;

static bool config_ok(const dsp_afe_aec_config_t *cfg)
{
    return cfg != NULL && cfg->n_mics >= 1 && cfg->n_mics <= GEN_ARRAY_N_MICS && cfg->n_partitions >= 2 &&
           cfg->n_partitions <= PARTITIONS_MAX && cfg->bulk_delay_samples <= GEN_AFE_AEC_BULK_DELAY_MAX;
}

size_t dsp_afe_aec_workspace_bytes(const dsp_afe_aec_config_t *cfg)
{
    if (!config_ok(cfg)) { return 0; }
    return afe_region_bytes(sizeof(struct dsp_afe_aec_s) + cfg->n_mics * sizeof(canceller_t));
}

static void start_canceller(canceller_t *c, uint8_t n_partitions)
{
    memset(c, 0, sizeof(*c));
    for (uint8_t j = 0; j < n_partitions; j++) {
        c->prop[j] = PROP_SUM / (float)n_partitions;
    }
    c->cross = c->autocorr = GEN_AFE_AEC_PY_FLOOR;
}

esp_err_t dsp_afe_aec_init(dsp_afe_aec_t **out, const dsp_afe_aec_config_t *cfg, dsp_spec_fft_t *fft,
                           void *mem, size_t bytes)
{
    if (out == NULL || fft == NULL || mem == NULL || !config_ok(cfg)) { return ESP_ERR_INVALID_ARG; }
    dsp_afe_aec_t *st = afe_state(mem, bytes, sizeof(*st) + cfg->n_mics * sizeof(canceller_t));
    if (st == NULL) { return ESP_ERR_INVALID_SIZE; }
    memset(st, 0, sizeof(*st));
    st->fft = fft;
    st->n_mics = cfg->n_mics;
    st->n_partitions = cfg->n_partitions;
    st->bulk_samples = cfg->bulk_delay_samples;
    for (size_t i = 0; i < N_FFT; i++) {
        st->window[i] = (float)(0.5 - 0.5 * afe_cos_series(2.0 * AFE_PI * (double)i / N_FFT));
    }
    dsp_afe_aec_reset(st);
    *out = st;
    return ESP_OK;
}

static float energy_of(const float *x, const float *y, size_t n)
{
    float sum = 0.0f;
    for (size_t i = 0; i < n; i++) {
        sum += x[i] * y[i];
    }
    return sum;
}

static void power_of(const dsp_spec_cplx_t *bins, float *power)
{
    for (size_t k = 0; k < N_BINS; k++) {
        power[k] = bins[k].re * bins[k].re + bins[k].im * bins[k].im;
    }
}

static void take_delayed(dsp_afe_aec_t *st, const float *ref)
{
    memcpy(st->delay + st->bulk_samples, ref, HOP * sizeof(float));
    memcpy(st->far, st->delay, HOP * sizeof(float));
    memmove(st->delay, st->delay + HOP, st->bulk_samples * sizeof(float));
}

static bool far_is_silent(const dsp_afe_aec_t *st)
{
    for (size_t i = 0; i < HOP; i++) {
        if (st->far[i] != 0.0f) { return false; }
    }
    return true;
}

static esp_err_t push_far(dsp_afe_aec_t *st)
{
    const uint8_t n = st->n_partitions;
    st->head = (uint8_t)((st->head + n - 1) % n);
    memcpy(st->block, st->previous, HOP * sizeof(float));
    memcpy(st->block + HOP, st->far, HOP * sizeof(float));
    const esp_err_t err = dsp_spec_fft_forward(st->fft, st->block, st->spectra[st->head]);
    if (err != ESP_OK) { return err; }
    memcpy(st->previous, st->far, HOP * sizeof(float));
    const float smooth = GEN_AFE_AEC_POWER_SMOOTH / (float)n;
    const dsp_spec_cplx_t *x = st->spectra[st->head];
    for (size_t k = 0; k < N_BINS; k++) {
        const float bin_power = x[k].re * x[k].re + x[k].im * x[k].im;
        st->power[k] = (1.0f - smooth) * st->power[k] + smooth * bin_power;
    }
    return ESP_OK;
}

static void filter_far(const dsp_afe_aec_t *st, const canceller_t *c, dsp_spec_cplx_t *out)
{
    memset(out, 0, N_BINS * sizeof(*out));
    for (uint8_t j = 0; j < st->n_partitions; j++) {
        const dsp_spec_cplx_t *x = st->spectra[(st->head + j) % st->n_partitions];
        const dsp_spec_cplx_t *w = c->weights[j];
        for (size_t k = 0; k < N_BINS; k++) {
            out[k].re = out[k].re + (x[k].re * w[k].re - x[k].im * w[k].im);
            out[k].im = out[k].im + (x[k].re * w[k].im + x[k].im * w[k].re);
        }
    }
}

// The leak: how much of the error's spectrum moves with the echo estimate's (Valin 2007).
static void track_leak(dsp_afe_aec_t *st, canceller_t *c, const energies_t *e, float err_floor)
{
    float pey = 0.0f;
    float pyy = 0.0f;
    const float average = GEN_AFE_AEC_SPEC_AVERAGE;
    for (size_t k = 0; k < N_BINS; k++) {
        const float err_dev = st->err_bin_power[k] - c->err_smooth[k];
        const float echo_dev = st->echo_bin_power[k] - c->echo_smooth[k];
        pey += err_dev * echo_dev;
        pyy += echo_dev * echo_dev;
        c->err_smooth[k] = (1.0f - average) * c->err_smooth[k] + average * st->err_bin_power[k];
        c->echo_smooth[k] = (1.0f - average) * c->echo_smooth[k] + average * st->echo_bin_power[k];
    }
    pyy = sqrtf(pyy);
    pey = pey / (pyy + GEN_AFE_AEC_TINY);
    const float beta = fminf(GEN_AFE_AEC_BETA0 * e->echo, GEN_AFE_AEC_BETA_MAX * err_floor);
    const float alpha = beta / err_floor;
    c->cross = (1.0f - alpha) * c->cross + alpha * pey;
    c->autocorr = (1.0f - alpha) * c->autocorr + alpha * pyy;
    c->autocorr = fmaxf(c->autocorr, GEN_AFE_AEC_PY_FLOOR);
    c->cross = fminf(fmaxf(c->cross, GEN_AFE_AEC_MIN_LEAK * c->autocorr), c->autocorr);
    c->leak = c->cross / c->autocorr;
}

static void set_rates(dsp_afe_aec_t *st, canceller_t *c, const energies_t *e)
{
    const float err_floor = fmaxf(e->err, GEN_AFE_AEC_ERR_FLOOR);
    track_leak(st, c, e, err_floor);
    float rer = (GEN_AFE_AEC_FAR_SHARE * e->far + GEN_AFE_AEC_ECHO_SHARE * c->leak * e->echo) / err_floor;
    rer = fmaxf(rer, e->cross * e->cross / (GEN_AFE_AEC_ERR_FLOOR + err_floor * e->echo));
    rer = fminf(rer, GEN_AFE_AEC_RER_MAX);
    if (!c->adapted && c->sum_adapt > (float)st->n_partitions &&
        c->leak * e->echo > ADAPTED_SHARE * e->echo) {
        c->adapted = true;
    }
    if (c->adapted) {
        for (size_t k = 0; k < N_BINS; k++) {
            const float bin_err = st->err_bin_power[k] + GEN_AFE_AEC_E_FLOOR;
            float r = fminf(c->leak * st->echo_bin_power[k], ECHO_CAP * bin_err);
            r = ECHO_MIX * r + RER_MIX * rer * bin_err;
            st->rate[k] = r / (bin_err * (st->power[k] + GEN_AFE_AEC_POWER_FLOOR));
        }
        return;
    }
    float rate = 0.0f;
    if (e->far > GEN_AFE_AEC_FAR_MIN) {
        rate = fminf(RATE_START * e->far, RATE_START * err_floor) / err_floor;
    }
    c->sum_adapt += rate;
    for (size_t k = 0; k < N_BINS; k++) {
        st->rate[k] = rate / (st->power[k] + GEN_AFE_AEC_POWER_FLOOR);
    }
}

static void update_proportions(dsp_afe_aec_t *st, canceller_t *c)
{
    float sizes[PARTITIONS_MAX];
    float largest = 1.0f;
    for (uint8_t j = 0; j < st->n_partitions; j++) {
        power_of(c->weights[j], st->rate);
        float sum = 0.0f;
        for (size_t k = 0; k < N_BINS; k++) {
            sum += st->rate[k];
        }
        sizes[j] = sqrtf(1.0f + sum);
        largest = fmaxf(largest, sizes[j]);
    }
    float sum = 0.0f;
    for (uint8_t j = 0; j < st->n_partitions; j++) {
        sizes[j] = sizes[j] + SIZE_SHARE * largest;
        sum += sizes[j];
    }
    for (uint8_t j = 0; j < st->n_partitions; j++) {
        c->prop[j] = PROP_SUM * sizes[j] / sum;
    }
}

static esp_err_t update_weights(dsp_afe_aec_t *st, canceller_t *c)
{
    const uint8_t n = st->n_partitions;
    for (size_t k = 0; k < N_BINS; k++) {
        st->err_bins[k].re = st->err_bins[k].re * st->rate[k];
        st->err_bins[k].im = st->err_bins[k].im * st->rate[k];
    }
    for (uint8_t j = 0; j < n; j++) {
        const dsp_spec_cplx_t *x = st->spectra[(st->head + j) % n];
        dsp_spec_cplx_t *w = c->weights[j];
        const float prop = c->prop[j];
        for (size_t k = 0; k < N_BINS; k++) {
            const dsp_spec_cplx_t *e = &st->err_bins[k];
            w[k].re = w[k].re + prop * (x[k].re * e->re + x[k].im * e->im);
            w[k].im = w[k].im + prop * (x[k].re * e->im - x[k].im * e->re);
        }
    }
    // One partition's gradient a block is held to a block's length: all of them would cost 2 n transforms.
    dsp_spec_cplx_t *held = c->weights[st->blocks % n];
    esp_err_t err = dsp_spec_fft_inverse(st->fft, held, st->block);
    if (err != ESP_OK) { return err; }
    memset(st->block + HOP, 0, HOP * sizeof(float));
    err = dsp_spec_fft_forward(st->fft, st->block, held);
    if (err != ESP_OK) { return err; }
    update_proportions(st, c);
    return ESP_OK;
}

static void watch(dsp_afe_aec_t *st, canceller_t *c, float mic_energy, float err_energy)
{
    const float keep = GEN_AFE_AEC_ERLE_KEEP;
    c->mic_power = keep * c->mic_power + (1.0f - keep) * mic_energy;
    c->err_power = keep * c->err_power + (1.0f - keep) * err_energy;
    c->over = err_energy > GEN_AFE_AEC_DIVERGE_RATIO * mic_energy ? c->over + 1 : 0;
    if (c->over < GEN_AFE_AEC_DIVERGE_BLOCKS) { return; }
    memset(c->weights, 0, sizeof(c->weights));
    c->adapted = false;
    c->sum_adapt = 0.0f;
    c->over = 0;
    st->stats.diverged = true;
    st->stats.divergence_resets++;
}

static esp_err_t residual_echo(dsp_afe_aec_t *st, canceller_t *c, const float *echo, float *residual_power)
{
    const float leak = fminf(LEAK_SCALE * c->leak, 1.0f);
    for (size_t i = 0; i < HOP; i++) {
        st->block[i] = c->echo_tail[i] * st->window[i];
        st->block[HOP + i] = echo[i] * st->window[HOP + i];
    }
    memcpy(c->echo_tail, echo, HOP * sizeof(float));
    const esp_err_t err = dsp_spec_fft_forward(st->fft, st->block, st->echo_bins);
    if (err != ESP_OK || residual_power == NULL) { return err; }
    power_of(st->echo_bins, st->rate);
    for (size_t k = 0; k < N_BINS; k++) {
        residual_power[k] = fmaxf(residual_power[k], leak * st->rate[k]);
    }
    return ESP_OK;
}

static esp_err_t cancel_mic(dsp_afe_aec_t *st, canceller_t *c, float *mic, float far_energy,
                            float *residual_power)
{
    filter_far(st, c, st->echo_bins);
    esp_err_t err = dsp_spec_fft_inverse(st->fft, st->echo_bins, st->block);
    if (err != ESP_OK) { return err; }
    float *echo = st->echo;
    memcpy(echo, st->block + HOP, HOP * sizeof(float));
    for (size_t i = 0; i < HOP; i++) {
        st->err[i] = mic[i] - echo[i];
    }
    const energies_t e = {
        .far = far_energy,
        .echo = energy_of(echo, echo, HOP),
        .err = energy_of(st->err, st->err, HOP),
        .cross = energy_of(st->err, echo, HOP),
    };
    const float mic_energy = energy_of(mic, mic, HOP);
    memset(st->block, 0, HOP * sizeof(float));
    memcpy(st->block + HOP, st->err, HOP * sizeof(float));
    err = dsp_spec_fft_forward(st->fft, st->block, st->err_bins);
    if (err != ESP_OK) { return err; }
    memcpy(st->block + HOP, echo, HOP * sizeof(float));
    err = dsp_spec_fft_forward(st->fft, st->block, st->echo_bins);
    if (err != ESP_OK) { return err; }
    power_of(st->err_bins, st->err_bin_power);
    power_of(st->echo_bins, st->echo_bin_power);
    set_rates(st, c, &e);
    err = update_weights(st, c);
    if (err != ESP_OK) { return err; }
    watch(st, c, mic_energy, e.err);
    memcpy(mic, st->err, HOP * sizeof(float));
    return residual_echo(st, c, echo, residual_power);
}

esp_err_t dsp_afe_aec_process(dsp_afe_aec_t *st, float *const *mic, const float *ref, float *residual_power)
{
    if (st == NULL || mic == NULL || ref == NULL) { return ESP_ERR_INVALID_ARG; }
    if (residual_power != NULL) { memset(residual_power, 0, N_BINS * sizeof(float)); }
    take_delayed(st, ref);
    st->silent_blocks = far_is_silent(st) ? st->silent_blocks + 1 : 0;
    // Nothing played for longer than the filter reaches: no echo is left to cancel, and no time is spent.
    if (st->silent_blocks > st->n_partitions) { return ESP_OK; }
    esp_err_t err = push_far(st);
    if (err != ESP_OK) { return err; }
    st->stats.diverged = false;
    const float far_energy = energy_of(st->far, st->far, HOP);
    float erle_db = 0.0f;
    for (uint8_t m = 0; m < st->n_mics && err == ESP_OK; m++) {
        canceller_t *c = &st->mics[m];
        err = cancel_mic(st, c, mic[m], far_energy, residual_power);
        erle_db += DB_PER_DECADE * log10f((c->mic_power + POWER_EPSILON) / (c->err_power + POWER_EPSILON));
    }
    st->stats.erle_db = erle_db / (float)st->n_mics;
    st->blocks++;
    return err;
}

void dsp_afe_aec_stats(const dsp_afe_aec_t *st, dsp_afe_aec_stats_t *out)
{
    *out = st->stats;
}

void dsp_afe_aec_flush(dsp_afe_aec_t *st)
{
    memset(st->spectra, 0, sizeof(st->spectra));
    memset(st->previous, 0, sizeof(st->previous));
    memset(st->delay, 0, sizeof(st->delay));
    st->silent_blocks = st->n_partitions + 1u;
    for (uint8_t m = 0; m < st->n_mics; m++) {
        memset(st->mics[m].echo_tail, 0, sizeof(st->mics[m].echo_tail));
    }
}

void dsp_afe_aec_reset(dsp_afe_aec_t *st)
{
    dsp_afe_aec_flush(st);
    memset(&st->stats, 0, sizeof(st->stats));
    memset(st->power, 0, sizeof(st->power));
    st->head = 0;
    st->blocks = 0;
    for (uint8_t m = 0; m < st->n_mics; m++) {
        start_canceller(&st->mics[m], st->n_partitions);
    }
}
