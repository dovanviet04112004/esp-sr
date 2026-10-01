#include "command_ctc/ctc_score.h"

#include <math.h>
#include <stdbool.h>
#include <string.h>

// Each product rounds on its own, as in numpy: a fused madd.s breaks bit parity (KEHOACH 3.12).
#pragma GCC optimize("fp-contract=off")

#define BLANK 0
#define MILLI 1000.0f
#define FIELD_MAX 65535 // the uint16 fields of ai_engine_command_result_t
#define STATES_MAX (2 * AI_ENGINE_COMMAND_CTC_UNITS_MAX + 1)
#define ZERO_EXP (-(1 << 30)) // the exponent of a zero, below any a live value reaches
#define DROP_BELOW 100        // orders under its sum's top: float32 rounds it away
#define EXP_MIN_NATS (-10000.0f)
#define EXP_MAX_NATS 10000.0f
#define LOG2E 0x1.715476p+0f
#define LN2_HI 0x1.63p-1f
#define LN2_LO (-0x1.bd0106p-13f)
#define LN2 0.6931471805599453

typedef struct {
    float m;   // 0, or in [1, 2)
    int32_t e; // the value is m * 2^e
} wide_t;

// Cephes expf: e^r = 1 + r + r^2 p(r) on |r| <= ln(2)/2, p highest degree first; the mirror holds the same.
static const float EXP_POLY[] = {0x1.a0d2cep-13f, 0x1.6e879cp-10f, 0x1.11121p-7f,
                                 0x1.555382p-5f,  0x1.555554p-3f,  0x1p-1f};

static float bits_float(uint32_t bits)
{
    float x;
    memcpy(&x, &bits, sizeof(x));
    return x;
}

static uint32_t float_bits(float x)
{
    uint32_t bits;
    memcpy(&bits, &x, sizeof(bits));
    return bits;
}

static wide_t normalized(float x, int32_t e)
{
    if (x == 0.0f) { return (wide_t){0.0f, ZERO_EXP}; }
    const uint32_t bits = float_bits(x);
    const int32_t own = (int32_t)((bits >> 23) & 0xffu) - 127;
    return (wide_t){bits_float((bits & 0x807fffffu) | (127u << 23)), e + own};
}

static wide_t exp_wide(float x)
{
    if (!(x >= EXP_MIN_NATS)) { return (wide_t){0.0f, ZERO_EXP}; }
    if (x > EXP_MAX_NATS) { x = EXP_MAX_NATS; }
    const float n = floorf(x * LOG2E + 0.5f);
    const float r = (x - n * LN2_HI) - n * LN2_LO;
    float poly = EXP_POLY[0];
    for (size_t i = 1; i < sizeof(EXP_POLY) / sizeof(EXP_POLY[0]); i++) {
        poly = poly * r + EXP_POLY[i];
    }
    poly = (poly * (r * r) + r) + 1.0f;
    return normalized(poly, (int32_t)n);
}

static float scaled(wide_t x, int32_t top)
{
    const int32_t d = x.e - top;
    return d < -DROP_BELOW ? 0.0f : x.m * bits_float((uint32_t)(d + 127) << 23);
}

static float sequence_score(const wide_t *probs, size_t n_classes, size_t n_frames,
                            const ai_engine_seq_t *seq)
{
    uint8_t labels[STATES_MAX];
    size_t n = 0;
    labels[n++] = BLANK;
    for (size_t k = 0; k < seq->n_units; k++) {
        labels[n++] = (uint8_t)(seq->units[k] + 1);
        labels[n++] = BLANK;
    }
    wide_t alpha[STATES_MAX];
    bool jump[STATES_MAX];
    for (size_t s = 0; s < n; s++) {
        alpha[s] = (wide_t){0.0f, ZERO_EXP};
        jump[s] = s >= 2 && labels[s] != BLANK && labels[s] != labels[s - 2];
    }
    alpha[0] = probs[BLANK];
    if (n > 1) { alpha[1] = probs[labels[1]]; }
    for (size_t t = 1; t < n_frames; t++) {
        const wide_t *frame = probs + t * n_classes;
        // Down from the last state, so each update still reads the previous frame.
        for (size_t s = n; s-- > 0;) {
            int32_t top = alpha[s].e;
            if (s >= 1 && alpha[s - 1].e > top) { top = alpha[s - 1].e; }
            if (jump[s] && alpha[s - 2].e > top) { top = alpha[s - 2].e; }
            float total = scaled(alpha[s], top);
            if (s >= 1) { total += scaled(alpha[s - 1], top); }
            if (jump[s]) { total += scaled(alpha[s - 2], top); }
            const wide_t p = frame[labels[s]];
            alpha[s] = normalized(total * p.m, top + p.e);
        }
    }
    int32_t top = alpha[n - 1].e;
    if (n > 1 && alpha[n - 2].e > top) { top = alpha[n - 2].e; }
    float total = scaled(alpha[n - 1], top);
    if (n > 1) { total += scaled(alpha[n - 2], top); }
    if (total == 0.0f) { return -INFINITY; }
    return (float)((log((double)total) + (double)top * LN2) / (double)n_frames);
}

static float free_score(const float *log_probs, size_t n_classes, size_t n_frames)
{
    float total = 0.0f;
    for (size_t t = 0; t < n_frames; t++) {
        const float *frame = log_probs + t * n_classes;
        float top = frame[0];
        for (size_t c = 1; c < n_classes; c++) {
            if (frame[c] > top) { top = frame[c]; }
        }
        total += top;
    }
    return total / (float)n_frames;
}

static uint16_t milli(float x)
{
    const float scaled_x = x * MILLI;
    if (!(scaled_x < (float)FIELD_MAX)) { return FIELD_MAX; }
    if (scaled_x <= 0.0f) { return 0; }
    return (uint16_t)lrintf(scaled_x);
}

static esp_err_t check(const ai_engine_lexicon_t *lexicon, size_t n_classes)
{
    for (size_t c = 0; c < lexicon->n_commands; c++) {
        if (lexicon->n_variants[c] == 0 || lexicon->n_variants[c] > AI_ENGINE_VARIANTS_MAX) {
            return ESP_ERR_INVALID_ARG;
        }
        for (size_t v = 0; v < lexicon->n_variants[c]; v++) {
            const ai_engine_seq_t *seq = &lexicon->variants[c][v];
            if (seq->n_units > AI_ENGINE_COMMAND_CTC_UNITS_MAX) { return ESP_ERR_INVALID_SIZE; }
            for (size_t k = 0; k < seq->n_units; k++) {
                if ((size_t)seq->units[k] + 1 >= n_classes) { return ESP_ERR_INVALID_ARG; }
            }
        }
    }
    return ESP_OK;
}

size_t ai_engine_command_ctc_work_bytes(size_t n_classes, size_t n_frames)
{
    return n_classes * n_frames * sizeof(wide_t);
}

esp_err_t ai_engine_command_ctc_decide(const float *log_probs, size_t n_classes, size_t n_frames,
                                       const ai_engine_lexicon_t *lexicon, uint16_t reject, uint16_t margin,
                                       void *work, float *scores, ai_engine_command_result_t *out)
{
    if (log_probs == NULL || lexicon == NULL || work == NULL || out == NULL || n_classes < 2 ||
        n_frames == 0 || lexicon->n_commands == 0 || lexicon->n_commands > AI_ENGINE_COMMANDS_MAX) {
        return ESP_ERR_INVALID_ARG;
    }
    const esp_err_t err = check(lexicon, n_classes);
    if (err != ESP_OK) { return err; }
    wide_t *probs = work;
    for (size_t i = 0; i < n_frames * n_classes; i++) {
        probs[i] = exp_wide(log_probs[i]);
    }
    size_t best = 0;
    float best_score = -INFINITY, second = -INFINITY;
    for (size_t c = 0; c < lexicon->n_commands; c++) {
        float score = -INFINITY;
        for (size_t v = 0; v < lexicon->n_variants[c]; v++) {
            const float s = sequence_score(probs, n_classes, n_frames, &lexicon->variants[c][v]);
            if (s > score) { score = s; }
        }
        if (scores != NULL) { scores[c] = score; }
        if (score > best_score) {
            second = best_score;
            best_score = score;
            best = c;
        } else if (score > second) {
            second = score;
        }
    }
    const bool reached = best_score > -INFINITY;
    const uint16_t gap = reached ? milli(free_score(log_probs, n_classes, n_frames) - best_score) : FIELD_MAX;
    const uint16_t lead = second > -INFINITY ? milli(best_score - second) : FIELD_MAX;
    const bool accepted = reached && gap <= reject && lead >= margin;
    *out = (ai_engine_command_result_t){
        .command = accepted ? (int16_t)best : AI_ENGINE_COMMAND_CTC_REJECTED,
        .score_permille = reached ? milli((float)exp((double)best_score)) : 0,
        .margin_permille = lead,
        .free_gap_permille = gap,
    };
    return ESP_OK;
}
