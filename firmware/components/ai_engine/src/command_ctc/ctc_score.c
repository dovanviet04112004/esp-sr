#include "command_ctc/ctc_score.h"

#include <math.h>
#include <stdbool.h>

#include "gen_units.h"

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
#define PREPARED 0x53524354u // "SRCT": prepare laid the commands out

typedef struct {
    float m;   // 0, or in [1, 2)
    int32_t e; // the value is m * 2^e
} wide_t;

// Cephes expf: e^r = 1 + r + r^2 p(r) on |r| <= ln(2)/2, p highest degree first; the mirror holds the same.
static const float EXP_POLY[] = {0x1.a0d2cep-13f, 0x1.6e879cp-10f, 0x1.11121p-7f,
                                 0x1.555382p-5f,  0x1.555554p-3f,  0x1p-1f};

// A union, not memcpy: GCC for the ESP32-S3 turns a 4-byte memcpy into a call, ~35 cycles each way.
typedef union {
    float f;
    uint32_t u;
} float_bits_t;

static float bits_float(uint32_t bits)
{
    return ((float_bits_t){.u = bits}).f;
}

static uint32_t float_bits(float x)
{
    return ((float_bits_t){.f = x}).u;
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

typedef struct {
    uint8_t n_states;
    uint8_t *labels;
    uint8_t *jump;
    wide_t *alpha;
} pass_t;

// The work area: every variant's pass, its states laid end to end in the three pools, the free loop's sum,
// then the window's probabilities frame by frame.
typedef struct {
    uint32_t prepared;
    uint32_t n_classes, n_commands, frames_cap, frames;
    float free_total;
    uint8_t n_variants[AI_ENGINE_COMMANDS_MAX];
    pass_t pass[AI_ENGINE_COMMANDS_MAX][AI_ENGINE_VARIANTS_MAX];
    wide_t alpha[AI_ENGINE_COMMAND_CTC_STATES_MAX];
    uint8_t labels[AI_ENGINE_COMMAND_CTC_STATES_MAX];
    uint8_t jump[AI_ENGINE_COMMAND_CTC_STATES_MAX];
    wide_t probs[];
} stream_t;

static void lay_out(pass_t *p, const ai_engine_seq_t *seq)
{
    size_t n = 0;
    p->labels[n++] = BLANK;
    for (size_t k = 0; k < seq->n_units; k++) {
        p->labels[n++] = (uint8_t)(seq->units[k] + 1);
        p->labels[n++] = BLANK;
    }
    p->n_states = (uint8_t)n;
    for (size_t s = 0; s < n; s++) {
        p->jump[s] = s >= 2 && p->labels[s] != BLANK && p->labels[s] != p->labels[s - 2];
        p->alpha[s] = (wide_t){0.0f, ZERO_EXP};
    }
}

static void pass_frame(pass_t *p, const wide_t *frame, bool first)
{
    const size_t n = p->n_states;
    if (first) {
        for (size_t s = 0; s < n; s++) {
            p->alpha[s] = (wide_t){0.0f, ZERO_EXP};
        }
        p->alpha[0] = frame[BLANK];
        if (n > 1) { p->alpha[1] = frame[p->labels[1]]; }
        return;
    }
    // Down from the last state, so each update still reads the previous frame.
    for (size_t s = n; s-- > 0;) {
        int32_t top = p->alpha[s].e;
        if (s >= 1 && p->alpha[s - 1].e > top) { top = p->alpha[s - 1].e; }
        if (p->jump[s] && p->alpha[s - 2].e > top) { top = p->alpha[s - 2].e; }
        float total = scaled(p->alpha[s], top);
        if (s >= 1) { total += scaled(p->alpha[s - 1], top); }
        if (p->jump[s]) { total += scaled(p->alpha[s - 2], top); }
        const wide_t q = frame[p->labels[s]];
        p->alpha[s] = normalized(total * q.m, top + q.e);
    }
}

static float pass_score(const pass_t *p, size_t per_frames)
{
    const size_t n = p->n_states;
    int32_t top = p->alpha[n - 1].e;
    if (n > 1 && p->alpha[n - 2].e > top) { top = p->alpha[n - 2].e; }
    float total = scaled(p->alpha[n - 1], top);
    if (n > 1) { total += scaled(p->alpha[n - 2], top); }
    if (total == 0.0f) { return -INFINITY; }
    return (float)((log((double)total) + (double)top * LN2) / (double)per_frames);
}

static float sequence_score(const wide_t *probs, size_t n_classes, size_t n_frames,
                            const ai_engine_seq_t *seq, size_t per_frames)
{
    uint8_t labels[STATES_MAX], jump[STATES_MAX];
    wide_t alpha[STATES_MAX];
    pass_t p = {.labels = labels, .jump = jump, .alpha = alpha};
    lay_out(&p, seq);
    for (size_t t = 0; t < n_frames; t++) {
        pass_frame(&p, probs + t * n_classes, t == 0);
    }
    return pass_score(&p, per_frames);
}

static bool ends_syllable(uint8_t unit)
{
    for (size_t k = 0; k < GEN_UNITS_N_TONES; k++) {
        if (unit == GEN_UNITS_TONES[k]) { return true; }
    }
    return false;
}

// A part said alone must not pass for the whole command (KEHOACH 3.12).
static bool outscored_by_a_part(const wide_t *probs, size_t n_classes, size_t n_frames,
                                const ai_engine_seq_t *variants, size_t n_variants, float score,
                                size_t per_frames)
{
    for (size_t v = 0; v < n_variants; v++) {
        const ai_engine_seq_t *seq = &variants[v];
        size_t ends[AI_ENGINE_COMMAND_CTC_UNITS_MAX + 1];
        size_t n_syllables = 0;
        for (size_t k = 0; k < seq->n_units; k++) {
            if (ends_syllable(seq->units[k])) { ends[n_syllables++] = k + 1; }
        }
        if (n_syllables == 0 || ends[n_syllables - 1] != seq->n_units) { ends[n_syllables++] = seq->n_units; }
        for (size_t first = 0; first < n_syllables; first++) {
            const size_t start = first == 0 ? 0 : ends[first - 1];
            for (size_t last = first; last < n_syllables - (first == 0); last++) {
                const ai_engine_seq_t part = {.n_units = (uint8_t)(ends[last] - start),
                                              .units = seq->units + start};
                if (sequence_score(probs, n_classes, n_frames, &part, per_frames) >= score) { return true; }
            }
        }
    }
    return false;
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

esp_err_t ai_engine_command_ctc_log_probs(const int8_t *logits, int exponent, size_t n_classes,
                                          size_t n_frames, float *log_probs)
{
    if (logits == NULL || log_probs == NULL || n_classes == 0 ||
        n_classes > AI_ENGINE_COMMAND_CTC_CLASSES_MAX) {
        return ESP_ERR_INVALID_ARG;
    }
    const float step = ldexpf(1.0f, exponent);
    wide_t probs[AI_ENGINE_COMMAND_CTC_CLASSES_MAX];
    for (size_t t = 0; t < n_frames; t++) {
        const int8_t *q = logits + t * n_classes;
        float *out = log_probs + t * n_classes;
        int8_t largest = q[0];
        for (size_t c = 1; c < n_classes; c++) {
            if (q[c] > largest) { largest = q[c]; }
        }
        const float top = (float)largest * step;
        int32_t top_e = ZERO_EXP;
        for (size_t c = 0; c < n_classes; c++) {
            out[c] = (float)q[c] * step - top;
            probs[c] = exp_wide(out[c]);
            if (probs[c].e > top_e) { top_e = probs[c].e; }
        }
        float total = 0.0f;
        for (size_t c = 0; c < n_classes; c++) {
            total += scaled(probs[c], top_e);
        }
        const float log_total = (float)(log((double)total) + (double)top_e * LN2);
        for (size_t c = 0; c < n_classes; c++) {
            out[c] -= log_total;
        }
    }
    return ESP_OK;
}

float ai_engine_command_ctc_exp(float x, int32_t *exponent)
{
    const wide_t w = exp_wide(x);
    *exponent = w.e;
    return w.m;
}

uint16_t ai_engine_command_ctc_milli(float x)
{
    return milli(x);
}

size_t ai_engine_command_ctc_work_bytes(size_t n_classes, size_t n_frames)
{
    return sizeof(stream_t) + n_classes * n_frames * sizeof(wide_t);
}

esp_err_t ai_engine_command_ctc_prepare(const ai_engine_lexicon_t *lexicon, size_t n_classes, size_t n_frames,
                                        void *work)
{
    if (lexicon == NULL || work == NULL || n_classes < 2 || n_classes > AI_ENGINE_COMMAND_CTC_CLASSES_MAX ||
        lexicon->n_commands == 0 || lexicon->n_commands > AI_ENGINE_COMMANDS_MAX) {
        return ESP_ERR_INVALID_ARG;
    }
    stream_t *st = work;
    st->prepared = 0;
    const esp_err_t err = check(lexicon, n_classes);
    if (err != ESP_OK) { return err; }
    size_t used = 0;
    for (size_t c = 0; c < lexicon->n_commands; c++) {
        st->n_variants[c] = lexicon->n_variants[c];
        for (size_t v = 0; v < lexicon->n_variants[c]; v++) {
            const size_t n = 2 * (size_t)lexicon->variants[c][v].n_units + 1;
            if (used + n > AI_ENGINE_COMMAND_CTC_STATES_MAX) { return ESP_ERR_INVALID_SIZE; }
            st->pass[c][v] =
                (pass_t){.labels = st->labels + used, .jump = st->jump + used, .alpha = st->alpha + used};
            lay_out(&st->pass[c][v], &lexicon->variants[c][v]);
            used += n;
        }
    }
    st->n_classes = (uint32_t)n_classes;
    st->n_commands = lexicon->n_commands;
    st->frames_cap = (uint32_t)n_frames;
    st->prepared = PREPARED;
    ai_engine_command_ctc_begin(work);
    return ESP_OK;
}

bool ai_engine_command_ctc_prepared_for(const ai_engine_lexicon_t *lexicon, const void *work)
{
    const stream_t *st = work;
    if (lexicon == NULL || st == NULL || st->prepared != PREPARED || st->n_commands != lexicon->n_commands) {
        return false;
    }
    for (size_t c = 0; c < st->n_commands; c++) {
        if (st->n_variants[c] != lexicon->n_variants[c]) { return false; }
        for (size_t v = 0; v < st->n_variants[c]; v++) {
            const ai_engine_seq_t *seq = &lexicon->variants[c][v];
            const pass_t *p = &st->pass[c][v];
            if (p->n_states != 2 * seq->n_units + 1) { return false; }
            for (size_t k = 0; k < seq->n_units; k++) {
                if (p->labels[2 * k + 1] != seq->units[k] + 1) { return false; }
            }
        }
    }
    return true;
}

void ai_engine_command_ctc_begin(void *work)
{
    stream_t *st = work;
    st->frames = 0;
    st->free_total = 0.0f;
}

esp_err_t ai_engine_command_ctc_frames(const float *log_probs, size_t n_frames, void *work)
{
    stream_t *st = work;
    if (log_probs == NULL || work == NULL) { return ESP_ERR_INVALID_ARG; }
    if (st->prepared != PREPARED) { return ESP_ERR_INVALID_STATE; }
    if (n_frames > st->frames_cap - st->frames) { return ESP_ERR_INVALID_SIZE; }
    const size_t classes = st->n_classes;
    for (size_t t = 0; t < n_frames; t++) {
        const float *frame = log_probs + t * classes;
        wide_t *probs = st->probs + (size_t)st->frames * classes;
        float top = frame[0];
        for (size_t c = 0; c < classes; c++) {
            probs[c] = exp_wide(frame[c]);
            if (frame[c] > top) { top = frame[c]; }
        }
        st->free_total += top;
        for (size_t c = 0; c < st->n_commands; c++) {
            for (size_t v = 0; v < st->n_variants[c]; v++) {
                pass_frame(&st->pass[c][v], probs, st->frames == 0);
            }
        }
        st->frames++;
    }
    return ESP_OK;
}

esp_err_t ai_engine_command_ctc_finish(const ai_engine_lexicon_t *lexicon, size_t per_frames, uint16_t reject,
                                       uint16_t margin, const void *work, float *scores,
                                       ai_engine_command_result_t *out)
{
    if (lexicon == NULL || work == NULL || out == NULL || per_frames == 0) { return ESP_ERR_INVALID_ARG; }
    const stream_t *st = work;
    if (st->prepared != PREPARED) { return ESP_ERR_INVALID_STATE; }
    if (lexicon->n_commands != st->n_commands) { return ESP_ERR_INVALID_ARG; }
    size_t best = 0;
    float best_score = -INFINITY, second = -INFINITY;
    for (size_t c = 0; c < st->n_commands; c++) {
        float score = -INFINITY;
        for (size_t v = 0; st->frames > 0 && v < st->n_variants[c]; v++) {
            const float s = pass_score(&st->pass[c][v], per_frames);
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
    const uint16_t gap = reached ? milli(st->free_total / (float)per_frames - best_score) : FIELD_MAX;
    const uint16_t lead = second > -INFINITY ? milli(best_score - second) : FIELD_MAX;
    const bool accepted = reached && gap <= reject && lead >= margin &&
                          !outscored_by_a_part(st->probs, st->n_classes, st->frames, lexicon->variants[best],
                                               lexicon->n_variants[best], best_score, per_frames);
    *out = (ai_engine_command_result_t){
        .command = accepted ? (int16_t)best : AI_ENGINE_COMMAND_CTC_REJECTED,
        .score_permille = reached ? milli((float)exp((double)best_score)) : 0,
        .margin_permille = lead,
        .free_gap_permille = gap,
    };
    return ESP_OK;
}

esp_err_t ai_engine_command_ctc_decide(const float *log_probs, size_t n_classes, size_t n_frames,
                                       size_t frames_cap, const ai_engine_lexicon_t *lexicon,
                                       size_t per_frames, uint16_t reject, uint16_t margin, void *work,
                                       float *scores, ai_engine_command_result_t *out)
{
    if (log_probs == NULL || out == NULL || n_frames == 0) { return ESP_ERR_INVALID_ARG; }
    esp_err_t err = ai_engine_command_ctc_prepare(lexicon, n_classes, frames_cap, work);
    if (err == ESP_OK) { err = ai_engine_command_ctc_frames(log_probs, n_frames, work); }
    if (err != ESP_OK) { return err; }
    return ai_engine_command_ctc_finish(lexicon, per_frames, reject, margin, work, scores, out);
}
