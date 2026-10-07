#include "dsp_afe/vad.h"

#include <math.h>
#include <string.h>

#include "afe_internal.h"
#include "gen_grid.h"

#define BANDS GEN_AFE_VAD_BANDS
#define GAUSSIANS GEN_AFE_VAD_GAUSSIANS
#define TABLE (GAUSSIANS * BANDS)
#define MIN_VALUES GEN_AFE_VAD_MIN_TRACK_VALUES
#define SPLIT_STAGES 5
#define PCM_FULL_SCALE 32768.0f
#define MS_PER_S 1000u
#define HOP GEN_GRID_HOP_SAMPLES

_Static_assert(HOP % 32 == 0, "six bands need five halvings of the hop");

static const float kNoiseWeights[] = GEN_AFE_VAD_NOISE_WEIGHTS;
static const float kSpeechWeights[] = GEN_AFE_VAD_SPEECH_WEIGHTS;
static const float kNoiseMeans[] = GEN_AFE_VAD_NOISE_MEANS_DB;
static const float kSpeechMeans[] = GEN_AFE_VAD_SPEECH_MEANS_DB;
static const float kNoiseStds[] = GEN_AFE_VAD_NOISE_STDS_DB;
static const float kSpeechStds[] = GEN_AFE_VAD_SPEECH_STDS_DB;
static const int kSpectrumWeights[] = GEN_AFE_VAD_SPECTRUM_WEIGHTS;
static const float kBandOffset[] = GEN_AFE_VAD_BAND_OFFSET_DB;
static const float kLocalThreshold[] = GEN_AFE_VAD_LOCAL_THRESHOLD;
static const float kGlobalThreshold[] = GEN_AFE_VAD_GLOBAL_THRESHOLD;
static const float kMeanMin[] = GEN_AFE_VAD_MEAN_MIN_DB;
static const float kNoiseGaussianMax[] = GEN_AFE_VAD_NOISE_GAUSSIAN_MAX_DB;
static const float kSpeechGaussianMax[] = GEN_AFE_VAD_SPEECH_GAUSSIAN_MAX_DB;
static const float kNoiseMeanMax[] = GEN_AFE_VAD_NOISE_MEAN_MAX_DB;
static const float kSpeechMeanMax[] = GEN_AFE_VAD_SPEECH_MEAN_MAX_DB;
static const float kMinSeparation[] = GEN_AFE_VAD_MIN_SEPARATION_DB;
static const float kDownsampleAllpass[] = GEN_AFE_VAD_DOWNSAMPLE_ALLPASS;
static const float kSplitAllpass[] = GEN_AFE_VAD_SPLIT_ALLPASS;
static const float kLowBandB[] = GEN_AFE_VAD_LOW_BAND_HPF_B;
static const float kLowBandA[] = GEN_AFE_VAD_LOW_BAND_HPF_A;

#define COUNT(a) (sizeof(a) / sizeof((a)[0]))
_Static_assert(COUNT(kNoiseMeans) == TABLE && COUNT(kSpeechStds) == TABLE,
               "Gaussian tables are [gaussian][band]");
_Static_assert(COUNT(kSpectrumWeights) == BANDS && COUNT(kSpeechGaussianMax) == BANDS, "one entry per band");
_Static_assert(COUNT(kLocalThreshold) == COUNT(kGlobalThreshold), "one threshold pair per aggressiveness");

typedef struct {
    float upper; // all-pass state of the even samples
    float lower; // of the odd samples
} half_band_t;

typedef struct {
    float x1, x2, y1, y2;
} low_band_hpf_t;

typedef struct {
    float weighted[GAUSSIANS];
    float delta[GAUSSIANS]; // (x - m) / s^2
} likelihoods_t;

struct dsp_afe_vad_s {
    float local_threshold;
    float global_threshold;
    uint32_t hangover_hops;
    uint32_t hangover_left;
    uint32_t hops_modelled;
    half_band_t down;
    half_band_t split[SPLIT_STAGES];
    low_band_hpf_t low_band;
    float noise_means[TABLE];
    float speech_means[TABLE];
    float noise_stds[TABLE];
    float speech_stds[TABLE];
    float min_values[BANDS][MIN_VALUES];
    int32_t min_ages[BANDS][MIN_VALUES];
    float min_mean[BANDS];
    float narrow[HOP / 2];
    float half_high[HOP / 4];
    float half_low[HOP / 4];
    float quarter_high[HOP / 8];
    float quarter_low[HOP / 8];
    float eighth_high[HOP / 16];
    float eighth_low[HOP / 16];
    float sixteenth_high[HOP / 32];
    float sixteenth_low[HOP / 32];
    float low_band_out[HOP / 32];
    float energy[BANDS];
    dsp_afe_vad_detail_t detail;
};

static bool config_ok(const dsp_afe_vad_config_t *cfg)
{
    return cfg != NULL && cfg->aggressiveness < COUNT(kLocalThreshold);
}

static float log2_linear(float x)
{
    int exponent = 0;
    const float mantissa = frexpf(x, &exponent);
    return (float)(exponent - 1) + (2.0f * mantissa - 1.0f);
}

static float exp2_linear_neg(float t)
{
    const float steps = GEN_AFE_VAD_DENSITY_STEPS;
    const float t_steps = floorf(t * steps);
    const float whole = ceilf(t_steps / steps);
    return floorf(ldexpf(steps + (whole * steps - t_steps), -(int)whole)) / steps;
}

static int floor_log2(float x)
{
    int exponent = 0;
    (void)frexpf(x, &exponent);
    return exponent - 1;
}

void dsp_afe_vad_reset(dsp_afe_vad_t *st)
{
    memset(&st->down, 0, sizeof(st->down));
    memset(st->split, 0, sizeof(st->split));
    memset(&st->low_band, 0, sizeof(st->low_band));
    memcpy(st->noise_means, kNoiseMeans, sizeof(kNoiseMeans));
    memcpy(st->speech_means, kSpeechMeans, sizeof(kSpeechMeans));
    memcpy(st->noise_stds, kNoiseStds, sizeof(kNoiseStds));
    memcpy(st->speech_stds, kSpeechStds, sizeof(kSpeechStds));
    for (size_t b = 0; b < BANDS; b++) {
        for (size_t i = 0; i < MIN_VALUES; i++) {
            st->min_values[b][i] = GEN_AFE_VAD_MIN_TRACK_EMPTY_DB;
            st->min_ages[b][i] = 0;
        }
        st->min_mean[b] = GEN_AFE_VAD_MIN_TRACK_START_DB;
    }
    st->hops_modelled = 0;
    st->hangover_left = 0;
    memset(&st->detail, 0, sizeof(st->detail));
}

size_t dsp_afe_vad_workspace_bytes(const dsp_afe_vad_config_t *cfg)
{
    return config_ok(cfg) ? afe_region_bytes(sizeof(struct dsp_afe_vad_s)) : 0;
}

esp_err_t dsp_afe_vad_init(dsp_afe_vad_t **out, const dsp_afe_vad_config_t *cfg, void *mem, size_t bytes)
{
    if (out == NULL || mem == NULL || !config_ok(cfg)) { return ESP_ERR_INVALID_ARG; }
    dsp_afe_vad_t *st = afe_state(mem, bytes, sizeof(*st));
    if (st == NULL) { return ESP_ERR_INVALID_SIZE; }
    memset(st, 0, sizeof(*st));
    st->local_threshold = kLocalThreshold[cfg->aggressiveness];
    st->global_threshold = kGlobalThreshold[cfg->aggressiveness];
    st->hangover_hops =
        ((uint32_t)cfg->hangover_ms * GEN_GRID_SAMPLE_RATE_HZ + MS_PER_S * HOP / 2) / (MS_PER_S * HOP);
    dsp_afe_vad_reset(st);
    *out = st;
    return ESP_OK;
}

// Even samples through one all-pass, odd through the other; high may be NULL when only the low half is
// wanted.
static void split(half_band_t *st, const float coef[2], const float *x, size_t n, float scale, float *high,
                  float *low)
{
    const float cu = coef[0], cl = coef[1];
    float su = st->upper, sl = st->lower;
    for (size_t i = 0; i < n / 2; i++) {
        const float xe = x[2 * i] * scale;
        const float xo = x[2 * i + 1] * scale;
        const float yu = cu * xe + su;
        su = xe - cu * yu;
        const float yl = cl * xo + sl;
        sl = xo - cl * yl;
        if (high != NULL) { high[i] = 0.5f * yu - 0.5f * yl; }
        low[i] = 0.5f * yu + 0.5f * yl;
    }
    st->upper = su;
    st->lower = sl;
}

static void low_band_hpf(low_band_hpf_t *st, const float *x, size_t n, float *out)
{
    for (size_t i = 0; i < n; i++) {
        const float y = kLowBandB[0] * x[i] + kLowBandB[1] * st->x1 + kLowBandB[2] * st->x2 -
                        kLowBandA[0] * st->y1 - kLowBandA[1] * st->y2;
        st->x2 = st->x1;
        st->x1 = x[i];
        st->y2 = st->y1;
        st->y1 = y;
        out[i] = y;
    }
}

static float energy_of(const float *x, size_t n)
{
    float energy = 0.0f;
    for (size_t i = 0; i < n; i++) {
        energy = energy + x[i] * x[i];
    }
    return energy;
}

// The 2-4 kHz half comes out mirrored, so its high output is 2-3 kHz: WebRTC's band 5, whatever its notes
// say.
static float features(dsp_afe_vad_t *st, const float *hop)
{
    split(&st->down, kDownsampleAllpass, hop, HOP, PCM_FULL_SCALE, NULL, st->narrow);
    split(&st->split[0], kSplitAllpass, st->narrow, HOP / 2, 1.0f, st->half_high, st->half_low);
    split(&st->split[1], kSplitAllpass, st->half_high, HOP / 4, 1.0f, st->quarter_high, st->quarter_low);
    st->energy[5] = energy_of(st->quarter_high, HOP / 8);
    st->energy[4] = energy_of(st->quarter_low, HOP / 8);
    split(&st->split[2], kSplitAllpass, st->half_low, HOP / 4, 1.0f, st->quarter_high, st->quarter_low);
    st->energy[3] = energy_of(st->quarter_high, HOP / 8);
    split(&st->split[3], kSplitAllpass, st->quarter_low, HOP / 8, 1.0f, st->eighth_high, st->eighth_low);
    st->energy[2] = energy_of(st->eighth_high, HOP / 16);
    split(&st->split[4], kSplitAllpass, st->eighth_low, HOP / 16, 1.0f, st->sixteenth_high,
          st->sixteenth_low);
    st->energy[1] = energy_of(st->sixteenth_high, HOP / 32);
    low_band_hpf(&st->low_band, st->sixteenth_low, HOP / 32, st->low_band_out);
    st->energy[0] = energy_of(st->low_band_out, HOP / 32);
    float total = 0.0f;
    // WebRTC's order, top band first; the sum only meets min_energy, but the order keeps it bit exact.
    for (size_t b = BANDS; b-- > 0;) {
        total = total + st->energy[b];
        st->detail.level_db[b] = kBandOffset[b];
        if (st->energy[b] > 0.0f) {
            const float level = GEN_AFE_VAD_DB_PER_LOG2 * log2_linear(st->energy[b]);
            st->detail.level_db[b] = fmaxf(level, 0.0f) + kBandOffset[b];
        }
    }
    return total;
}

static void likelihoods(float x, const float *weights, const float *means, const float *stds, size_t b,
                        likelihoods_t *out)
{
    for (size_t k = 0; k < GAUSSIANS; k++) {
        const size_t g = k * BANDS + b;
        const float inv_std = 1.0f / stds[g];
        const float diff = x - means[g];
        const float d = (inv_std * inv_std) * diff;
        const float exponent = 0.5f * (d * diff);
        float density = 0.0f;
        if (exponent < GEN_AFE_VAD_EXPONENT_LIMIT) {
            density = exp2_linear_neg(GEN_AFE_VAD_LOG2_E * exponent);
        }
        out->weighted[k] = weights[g] * (inv_std * density);
        out->delta[k] = d;
    }
}

static float total_of(const likelihoods_t *l)
{
    return l->weighted[0] + l->weighted[1];
}

// Below posterior_floor the noise class gives the whole share to its first Gaussian, the speech class none.
static void posteriors(const likelihoods_t *l, bool first_when_low, float gamma[GAUSSIANS])
{
    const float total = total_of(l);
    if (total >= GEN_AFE_VAD_POSTERIOR_FLOOR) {
        gamma[0] = l->weighted[0] / total;
        gamma[1] = 1.0f - gamma[0];
    } else {
        gamma[0] = first_when_low ? 1.0f : 0.0f;
        gamma[1] = 0.0f;
    }
}

static float track_minimum(dsp_afe_vad_t *st, size_t b, float x)
{
    float *values = st->min_values[b];
    int32_t *ages = st->min_ages[b];
    const size_t last = MIN_VALUES - 1;
    for (size_t i = 0; i < MIN_VALUES; i++) {
        if (ages[i] != GEN_AFE_VAD_MIN_TRACK_WINDOW_HOPS) {
            ages[i]++;
        } else {
            memmove(&values[i], &values[i + 1], (last - i) * sizeof(values[0]));
            memmove(&ages[i], &ages[i + 1], (last - i) * sizeof(ages[0]));
            ages[last] = GEN_AFE_VAD_MIN_TRACK_WINDOW_HOPS + 1;
            values[last] = GEN_AFE_VAD_MIN_TRACK_EMPTY_DB;
        }
    }
    size_t position = 0;
    while (position < MIN_VALUES && !(x < values[position])) {
        position++;
    }
    if (position < MIN_VALUES) {
        memmove(&values[position + 1], &values[position], (last - position) * sizeof(values[0]));
        memmove(&ages[position + 1], &ages[position], (last - position) * sizeof(ages[0]));
        values[position] = x;
        ages[position] = 1;
    }
    float median = GEN_AFE_VAD_MIN_TRACK_START_DB;
    if (st->hops_modelled > GEN_AFE_VAD_MIN_TRACK_MEDIAN_INDEX) {
        median = values[GEN_AFE_VAD_MIN_TRACK_MEDIAN_INDEX];
    } else if (st->hops_modelled > 0) {
        median = values[0];
    }
    float keep = GEN_AFE_VAD_MIN_SMOOTH_UP;
    if (st->hops_modelled == 0) {
        keep = GEN_AFE_VAD_MIN_SMOOTH_FIRST;
    } else if (median < st->min_mean[b]) {
        keep = GEN_AFE_VAD_MIN_SMOOTH_DOWN;
    }
    st->min_mean[b] = keep * st->min_mean[b] + (1.0f - keep) * median;
    return st->min_mean[b];
}

static float global_mean(const float *weights, const float *means, size_t b)
{
    return weights[b] * means[b] + weights[BANDS + b] * means[BANDS + b];
}

// Keep the speech model above the noise model by min_separation_db, and both under their caps.
static void separate(dsp_afe_vad_t *st, size_t b)
{
    float noise_global = global_mean(kNoiseWeights, st->noise_means, b);
    float speech_global = global_mean(kSpeechWeights, st->speech_means, b);
    const float gap = speech_global - noise_global;
    if (gap < kMinSeparation[b]) {
        const float short_db = kMinSeparation[b] - gap;
        for (size_t k = 0; k < GAUSSIANS; k++) {
            st->speech_means[k * BANDS + b] =
                st->speech_means[k * BANDS + b] + GEN_AFE_VAD_SEPARATION_SPEECH_SHARE * short_db;
            st->noise_means[k * BANDS + b] =
                st->noise_means[k * BANDS + b] - GEN_AFE_VAD_SEPARATION_NOISE_SHARE * short_db;
        }
        noise_global = global_mean(kNoiseWeights, st->noise_means, b);
        speech_global = global_mean(kSpeechWeights, st->speech_means, b);
    }
    if (speech_global > kSpeechMeanMax[b]) {
        const float excess = speech_global - kSpeechMeanMax[b];
        for (size_t k = 0; k < GAUSSIANS; k++) {
            st->speech_means[k * BANDS + b] = st->speech_means[k * BANDS + b] - excess;
        }
    }
    if (noise_global > kNoiseMeanMax[b]) {
        const float excess = noise_global - kNoiseMeanMax[b];
        for (size_t k = 0; k < GAUSSIANS; k++) {
            st->noise_means[k * BANDS + b] = st->noise_means[k * BANDS + b] - excess;
        }
    }
}

static void adapt_band(dsp_afe_vad_t *st, size_t b, float x, bool raw, const likelihoods_t *noise,
                       const likelihoods_t *speech)
{
    const float floor_db = track_minimum(st, b, x);
    const float noise_global = global_mean(kNoiseWeights, st->noise_means, b);
    float gamma_noise[GAUSSIANS];
    float gamma_speech[GAUSSIANS];
    posteriors(noise, true, gamma_noise);
    posteriors(speech, false, gamma_speech);
    for (size_t k = 0; k < GAUSSIANS; k++) {
        const size_t g = k * BANDS + b;
        const float mn = st->noise_means[g], ms = st->speech_means[g];
        const float sn = st->noise_stds[g], ss = st->speech_stds[g];
        float moved = mn;
        if (!raw) { moved = mn + (gamma_noise[k] * noise->delta[k]) * GEN_AFE_VAD_NOISE_MEAN_STEP; }
        moved = moved + GEN_AFE_VAD_FLOOR_PULL * (floor_db - noise_global);
        moved = fmaxf(moved, kMeanMin[k]);
        st->noise_means[g] = fminf(moved, kNoiseGaussianMax[g]);
        if (raw) {
            const float ms2 =
                fmaxf(ms + (gamma_speech[k] * speech->delta[k]) * GEN_AFE_VAD_SPEECH_MEAN_STEP, kMeanMin[k]);
            st->speech_means[g] = fminf(ms2, kSpeechGaussianMax[b]);
            const float grown = ss + ((gamma_speech[k] * (speech->delta[k] * (x - ms) - 1.0f)) / ss) *
                                         GEN_AFE_VAD_SPEECH_STD_STEP;
            st->speech_stds[g] = fmaxf(grown, GEN_AFE_VAD_MIN_STD_DB);
        } else {
            const float grown = sn + ((gamma_noise[k] * (noise->delta[k] * (x - mn) - 1.0f)) / sn) *
                                         GEN_AFE_VAD_NOISE_STD_STEP;
            st->noise_stds[g] = fmaxf(grown, GEN_AFE_VAD_MIN_STD_DB);
        }
    }
    separate(st, b);
}

static bool decide_and_adapt(dsp_afe_vad_t *st)
{
    likelihoods_t noise[BANDS];
    likelihoods_t speech[BANDS];
    const float *level = st->detail.level_db;
    for (size_t b = 0; b < BANDS; b++) {
        likelihoods(level[b], kNoiseWeights, st->noise_means, st->noise_stds, b, &noise[b]);
        likelihoods(level[b], kSpeechWeights, st->speech_means, st->speech_stds, b, &speech[b]);
    }
    bool raw = false;
    float llr_sum = 0.0f;
    for (size_t b = 0; b < BANDS; b++) {
        const float speech_total = fmaxf(total_of(&speech[b]), GEN_AFE_VAD_LIKELIHOOD_FLOOR);
        const float noise_total = fmaxf(total_of(&noise[b]), GEN_AFE_VAD_LIKELIHOOD_FLOOR);
        const int llr = floor_log2(speech_total) - floor_log2(noise_total);
        raw = raw || (float)llr > st->local_threshold;
        llr_sum = llr_sum + (float)(llr * kSpectrumWeights[b]);
    }
    raw = raw || llr_sum >= st->global_threshold;
    for (size_t b = 0; b < BANDS; b++) {
        adapt_band(st, b, level[b], raw, &noise[b], &speech[b]);
    }
    st->hops_modelled++;
    return raw;
}

esp_err_t dsp_afe_vad_process(dsp_afe_vad_t *st, const float *hop, bool *speech)
{
    if (st == NULL || hop == NULL || speech == NULL) { return ESP_ERR_INVALID_ARG; }
    const float total = features(st, hop);
    st->detail.raw = total > GEN_AFE_VAD_MIN_ENERGY && decide_and_adapt(st);
    if (st->detail.raw) {
        st->hangover_left = st->hangover_hops;
        *speech = true;
    } else if (st->hangover_left > 0) {
        st->hangover_left--;
        *speech = true;
    } else {
        *speech = false;
    }
    return ESP_OK;
}

void dsp_afe_vad_detail(const dsp_afe_vad_t *st, dsp_afe_vad_detail_t *out)
{
    *out = st->detail;
}
