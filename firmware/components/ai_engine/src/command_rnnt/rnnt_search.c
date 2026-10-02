#include "command_rnnt/rnnt_search.h"

#include <math.h>
#include <stdbool.h>
#include <string.h>

#include "command_ctc/ctc_score.h"
#include "gen_units.h"

#define BLANK 0
#define NONE 0xffffu
#define FIELD_MAX 65535 // the uint16 fields of ai_engine_command_result_t
#define STATES AI_ENGINE_COMMAND_RNNT_STATES_MAX
#define CONTEXT AI_ENGINE_COMMAND_RNNT_CONTEXT
#define UNITS_MAX AI_ENGINE_COMMAND_RNNT_UNITS_MAX
#define REGISTER_SLOTS (2 * STATES) // open addressing at half load at most

// The prefix tree, then the minimal FST in arcs by state; siblings stay in unit order throughout.
typedef struct {
    uint16_t n_tree, n_states;
    uint16_t child[STATES], sibling[STATES], canonical[STATES], rep[STATES], number[STATES], order[STATES];
    uint8_t unit[STATES], final_tree[STATES];
    uint16_t slot[REGISTER_SLOTS];
    uint16_t first_arc[STATES + 1], arc_to[STATES];
    uint8_t arc_unit[STATES], final[STATES];
} fst_t;

typedef struct {
    float score;
    uint16_t state;
    uint8_t n;
    uint8_t units[UNITS_MAX];
} hyp_t;

size_t ai_engine_command_rnnt_fst_bytes(void)
{
    return sizeof(fst_t);
}

static bool ends_syllable(uint8_t unit)
{
    for (size_t k = 0; k < GEN_UNITS_N_TONES; k++) {
        if (unit == GEN_UNITS_TONES[k]) { return true; }
    }
    return false;
}

// Where each syllable of seq ends, one past its tone unit, the last at the end whatever it is; their count.
static size_t syllable_ends(const ai_engine_seq_t *seq, size_t *ends)
{
    size_t n = 0;
    for (size_t k = 0; k < seq->n_units; k++) {
        if (ends_syllable(seq->units[k])) { ends[n++] = k + 1; }
    }
    if (n == 0 || ends[n - 1] != seq->n_units) { ends[n++] = seq->n_units; }
    return n;
}

static uint16_t child_of(fst_t *f, uint16_t node, uint8_t u)
{
    uint16_t before = NONE, at = f->child[node];
    while (at != NONE && f->unit[at] < u) {
        before = at;
        at = f->sibling[at];
    }
    if (at != NONE && f->unit[at] == u) { return at; }
    if (f->n_tree >= STATES) { return NONE; }
    const uint16_t made = f->n_tree++;
    f->child[made] = NONE;
    f->sibling[made] = at;
    f->unit[made] = u;
    f->final_tree[made] = 0;
    if (before == NONE) {
        f->child[node] = made;
    } else {
        f->sibling[before] = made;
    }
    return made;
}

static bool insert(fst_t *f, const uint8_t *units, size_t n)
{
    uint16_t node = 0;
    for (size_t k = 0; k < n; k++) {
        node = child_of(f, node, units[k]);
        if (node == NONE) { return false; }
    }
    f->final_tree[node] = 1;
    return true;
}

static uint32_t signature_hash(const fst_t *f, uint16_t node)
{
    uint32_t h = 2166136261u ^ f->final_tree[node];
    for (uint16_t c = f->child[node]; c != NONE; c = f->sibling[c]) {
        h = (h ^ f->unit[c]) * 16777619u;
        h = (h ^ f->canonical[c]) * 16777619u;
    }
    return h;
}

static bool same_signature(const fst_t *f, uint16_t a, uint16_t b)
{
    if (f->final_tree[a] != f->final_tree[b]) { return false; }
    uint16_t x = f->child[a], y = f->child[b];
    for (; x != NONE && y != NONE; x = f->sibling[x], y = f->sibling[y]) {
        if (f->unit[x] != f->unit[y] || f->canonical[x] != f->canonical[y]) { return false; }
    }
    return x == NONE && y == NONE;
}

// Children are made after their parent, so walking the states downward meets each child ahead of it.
static void minimise(fst_t *f)
{
    uint16_t ids = 0;
    for (size_t i = 0; i < REGISTER_SLOTS; i++) {
        f->slot[i] = NONE;
    }
    for (size_t s = f->n_tree; s-- > 0;) {
        size_t i = signature_hash(f, (uint16_t)s) % REGISTER_SLOTS;
        while (f->slot[i] != NONE && !same_signature(f, f->rep[f->slot[i]], (uint16_t)s)) {
            i = (i + 1) % REGISTER_SLOTS;
        }
        if (f->slot[i] == NONE) {
            f->slot[i] = ids;
            f->rep[ids++] = (uint16_t)s;
        }
        f->canonical[s] = f->slot[i];
    }
    for (size_t k = 0; k < ids; k++) {
        f->number[k] = NONE;
    }
    uint16_t count = 0;
    f->number[f->canonical[0]] = count;
    f->order[count++] = f->canonical[0];
    uint16_t arcs = 0;
    for (uint16_t k = 0; k < count; k++) {
        const uint16_t rep = f->rep[f->order[k]];
        f->first_arc[k] = arcs;
        f->final[k] = f->final_tree[rep];
        for (uint16_t c = f->child[rep]; c != NONE; c = f->sibling[c]) {
            const uint16_t to = f->canonical[c];
            if (f->number[to] == NONE) {
                f->number[to] = count;
                f->order[count++] = to;
            }
            f->arc_unit[arcs] = f->unit[c];
            f->arc_to[arcs++] = f->number[to];
        }
    }
    f->first_arc[count] = arcs;
    f->n_states = count;
}

esp_err_t ai_engine_command_rnnt_build(const ai_engine_lexicon_t *lexicon, void *fst, size_t bytes)
{
    if (lexicon == NULL || fst == NULL || bytes < sizeof(fst_t) || lexicon->n_commands == 0 ||
        lexicon->n_commands > AI_ENGINE_COMMANDS_MAX) {
        return ESP_ERR_INVALID_ARG;
    }
    fst_t *f = fst;
    f->n_tree = 1;
    f->child[0] = NONE;
    f->sibling[0] = NONE;
    f->final_tree[0] = 0;
    for (size_t c = 0; c < lexicon->n_commands; c++) {
        if (lexicon->n_variants[c] == 0 || lexicon->n_variants[c] > AI_ENGINE_VARIANTS_MAX) {
            return ESP_ERR_INVALID_ARG;
        }
        for (size_t v = 0; v < lexicon->n_variants[c]; v++) {
            const ai_engine_seq_t *seq = &lexicon->variants[c][v];
            if (seq->n_units > UNITS_MAX) { return ESP_ERR_INVALID_ARG; }
            if (!insert(f, seq->units, seq->n_units)) { return ESP_ERR_NO_MEM; }
            size_t ends[UNITS_MAX + 1];
            const size_t n = syllable_ends(seq, ends);
            for (size_t first = 0; first < n; first++) {
                const size_t start = first == 0 ? 0 : ends[first - 1];
                for (size_t last = first; last < n - (first == 0); last++) {
                    if (!insert(f, seq->units + start, ends[last] - start)) { return ESP_ERR_NO_MEM; }
                }
            }
        }
    }
    minimise(f);
    return ESP_OK;
}

size_t ai_engine_command_rnnt_work_bytes(size_t beam, size_t n_classes)
{
    return (2 * beam + beam * n_classes) * sizeof(hyp_t) + n_classes * sizeof(float);
}

// The predictor's last CONTEXT classes of a hypothesis: pad, then blank, then each unit's class.
static void context_of(const hyp_t *h, uint8_t pad, uint8_t *context)
{
    for (size_t k = 0; k < CONTEXT; k++) {
        const ptrdiff_t at = (ptrdiff_t)h->n - (ptrdiff_t)CONTEXT + (ptrdiff_t)k;
        context[k] = at >= 0 ? (uint8_t)(h->units[at] + 1) : at == -1 ? BLANK : pad;
    }
}

static float log_add(float a, float b)
{
    const float top = a >= b ? a : b;
    const float low = a >= b ? b : a;
    int32_t e;
    const float m = ai_engine_command_ctc_exp(low - top, &e);
    const float tail = m == 0.0f ? 0.0f : ldexpf(m, e);
    return top + (float)log(1.0 + (double)tail);
}

// Adds a candidate, or adds its probability into the earlier one of the same units, keeping that one's place.
static void offer(hyp_t *found, size_t *n_found, const hyp_t *from, int unit, float score, uint16_t state)
{
    const uint8_t n = (uint8_t)(from->n + (unit >= 0));
    for (size_t i = 0; i < *n_found; i++) {
        hyp_t *h = &found[i];
        if (h->n == n && memcmp(h->units, from->units, from->n) == 0 &&
            (unit < 0 || h->units[n - 1] == (uint8_t)unit)) {
            h->score = log_add(h->score, score);
            return;
        }
    }
    hyp_t *h = &found[(*n_found)++];
    memcpy(h->units, from->units, from->n);
    if (unit >= 0) { h->units[n - 1] = (uint8_t)unit; }
    h->n = n;
    h->score = score;
    h->state = state;
}

// The beam best first, ties in the order they arose.
static size_t keep_best(const hyp_t *found, size_t n_found, size_t beam, hyp_t *kept)
{
    size_t n = 0;
    for (size_t i = 0; i < n_found; i++) {
        size_t at = n;
        while (at > 0 && kept[at - 1].score < found[i].score) {
            at--;
        }
        if (at >= beam) { continue; }
        const size_t moved = n < beam ? n - at : n - at - 1;
        memmove(&kept[at + 1], &kept[at], moved * sizeof(hyp_t));
        kept[at] = found[i];
        n = n < beam ? n + 1 : n;
    }
    return n;
}

static esp_err_t search(const fst_t *f, size_t n_classes, size_t n_frames, size_t beam, uint8_t pad,
                        ai_engine_command_rnnt_log_probs_t log_probs, void *ctx, hyp_t *hyps, hyp_t *found,
                        float *lp, size_t *n_hyps)
{
    hyps[0] = (hyp_t){.score = 0.0f, .state = 0, .n = 0};
    *n_hyps = 1;
    uint8_t context[CONTEXT];
    for (size_t t = 0; t < n_frames; t++) {
        size_t n_found = 0;
        for (size_t k = 0; k < *n_hyps; k++) {
            const hyp_t *h = &hyps[k];
            context_of(h, pad, context);
            const esp_err_t err = log_probs(ctx, t, context, lp);
            if (err != ESP_OK) { return err; }
            offer(found, &n_found, h, -1, h->score + lp[BLANK], h->state);
            for (uint16_t a = f->first_arc[h->state]; a < f->first_arc[h->state + 1]; a++) {
                const uint8_t u = f->arc_unit[a];
                if ((size_t)u + 1 >= n_classes || h->n >= UNITS_MAX) { return ESP_ERR_INVALID_SIZE; }
                offer(found, &n_found, h, u, h->score + lp[u + 1], f->arc_to[a]);
            }
        }
        *n_hyps = keep_best(found, n_found, beam, hyps);
    }
    return ESP_OK;
}

static esp_err_t greedy(size_t n_classes, size_t n_frames, uint8_t pad,
                        ai_engine_command_rnnt_log_probs_t log_probs, void *ctx, float *lp, float *total)
{
    uint8_t context[CONTEXT];
    for (size_t k = 0; k < CONTEXT; k++) {
        context[k] = k + 1 == CONTEXT ? BLANK : pad;
    }
    *total = 0.0f;
    for (size_t t = 0; t < n_frames; t++) {
        const esp_err_t err = log_probs(ctx, t, context, lp);
        if (err != ESP_OK) { return err; }
        size_t best = 0;
        for (size_t c = 1; c < n_classes; c++) {
            if (lp[c] > lp[best]) { best = c; }
        }
        *total += lp[best];
        if (best != BLANK) {
            memmove(context, context + 1, CONTEXT - 1);
            context[CONTEXT - 1] = (uint8_t)best;
        }
    }
    return ESP_OK;
}

// Whether units are a variant of command c, and whether they are a run of whole syllables of one short of it.
static void ends_on(const ai_engine_lexicon_t *lexicon, size_t c, const hyp_t *h, bool *variant, bool *part)
{
    *variant = *part = false;
    for (size_t v = 0; v < lexicon->n_variants[c]; v++) {
        const ai_engine_seq_t *seq = &lexicon->variants[c][v];
        *variant |= seq->n_units == h->n && memcmp(seq->units, h->units, h->n) == 0;
        size_t ends[UNITS_MAX + 1];
        const size_t n = syllable_ends(seq, ends);
        for (size_t first = 0; first < n; first++) {
            const size_t start = first == 0 ? 0 : ends[first - 1];
            for (size_t last = first; last < n - (first == 0); last++) {
                *part |= ends[last] - start == h->n && memcmp(seq->units + start, h->units, h->n) == 0;
            }
        }
    }
}

esp_err_t ai_engine_command_rnnt_decide(const ai_engine_lexicon_t *lexicon, const void *fst, size_t n_classes,
                                        size_t n_frames, size_t beam, uint8_t pad,
                                        ai_engine_command_rnnt_log_probs_t log_probs, void *ctx,
                                        uint16_t reject, uint16_t margin, void *work, float *scores,
                                        ai_engine_command_result_t *out)
{
    if (lexicon == NULL || fst == NULL || log_probs == NULL || work == NULL || out == NULL || n_classes < 2 ||
        beam == 0 || beam > AI_ENGINE_COMMAND_RNNT_BEAM_MAX || lexicon->n_commands == 0 ||
        lexicon->n_commands > AI_ENGINE_COMMANDS_MAX) {
        return ESP_ERR_INVALID_ARG;
    }
    const fst_t *f = fst;
    hyp_t *hyps = work;
    hyp_t *found = hyps + beam;
    float *lp = (float *)(found + beam * n_classes + beam);
    float best_of[AI_ENGINE_COMMANDS_MAX], part_of[AI_ENGINE_COMMANDS_MAX];
    for (size_t c = 0; c < lexicon->n_commands; c++) {
        best_of[c] = part_of[c] = -INFINITY;
    }
    size_t n_hyps = 0;
    if (n_frames > 0) {
        const esp_err_t err =
            search(f, n_classes, n_frames, beam, pad, log_probs, ctx, hyps, found, lp, &n_hyps);
        if (err != ESP_OK) { return err; }
    }
    for (size_t k = 0; k < n_hyps; k++) {
        if (!f->final[hyps[k].state]) { continue; }
        const float per_frame = hyps[k].score / (float)n_frames;
        for (size_t c = 0; c < lexicon->n_commands; c++) {
            bool variant, part;
            ends_on(lexicon, c, &hyps[k], &variant, &part);
            if (variant && per_frame > best_of[c]) { best_of[c] = per_frame; }
            if (part && per_frame > part_of[c]) { part_of[c] = per_frame; }
        }
    }
    size_t best = 0;
    for (size_t c = 0; c < lexicon->n_commands; c++) {
        if (scores != NULL) { scores[c] = best_of[c]; }
        if (best_of[c] > best_of[best]) { best = c; }
    }
    if (!(best_of[best] > -INFINITY)) {
        *out = (ai_engine_command_result_t){AI_ENGINE_COMMAND_CTC_REJECTED, 0, FIELD_MAX, FIELD_MAX};
        return ESP_OK;
    }
    float second = -INFINITY;
    for (size_t c = 0; c < lexicon->n_commands; c++) {
        if (c != best && best_of[c] > second) { second = best_of[c]; }
    }
    float free_total;
    const esp_err_t err = greedy(n_classes, n_frames, pad, log_probs, ctx, lp, &free_total);
    if (err != ESP_OK) { return err; }
    const float free_score = free_total / (float)n_frames;
    const uint16_t gap = ai_engine_command_ctc_milli(free_score - best_of[best]);
    const uint16_t lead =
        second > -INFINITY ? ai_engine_command_ctc_milli(best_of[best] - second) : FIELD_MAX;
    const bool accepted = gap <= reject && lead >= margin && part_of[best] < best_of[best];
    *out = (ai_engine_command_result_t){
        .command = accepted ? (int16_t)best : AI_ENGINE_COMMAND_CTC_REJECTED,
        .score_permille = ai_engine_command_ctc_milli((float)exp((double)best_of[best])),
        .margin_permille = lead,
        .free_gap_permille = gap,
    };
    return ESP_OK;
}
