#include "command_rnnt/rnnt_search.h"

#include <math.h>
#include <stdbool.h>
#include <string.h>

#include "command_ctc/ctc_score.h"
#include "gen_units.h"

#define BLANK 0
#define NONE 0xffffu
#define PAD_MARK 0xffu  // the root's older context class: the caller's pad
#define FIELD_MAX 65535 // the uint16 fields of ai_engine_command_result_t
#define NODES AI_ENGINE_COMMAND_RNNT_NODES_MAX
#define CONTEXTS AI_ENGINE_COMMAND_RNNT_CONTEXTS_MAX
#define CONTEXT AI_ENGINE_COMMAND_RNNT_CONTEXT
#define UNITS_MAX AI_ENGINE_COMMAND_RNNT_UNITS_MAX
#define FREE_UNITS AI_ENGINE_COMMAND_RNNT_FREE_UNITS

_Static_assert(CONTEXT == 2, "a node's context is its parent's newer class and its own unit's");

// The prefix tree as built, siblings in unit order, then laid out breadth first with each node's context.
typedef struct {
    uint16_t n_built, n_nodes, n_contexts;
    uint16_t child[NODES], sibling[NODES], order[NODES], parent[NODES];
    uint8_t unit[NODES];
    uint16_t first_arc[NODES + 1], arc_to[NODES], context[NODES];
    uint8_t arc_unit[NODES], newer[NODES];
    uint8_t classes[CONTEXTS][CONTEXT];
} tree_t;

size_t ai_engine_command_rnnt_tree_bytes(void)
{
    return sizeof(tree_t);
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

static uint16_t child_of(tree_t *f, uint16_t node, uint8_t u)
{
    uint16_t before = NONE, at = f->child[node];
    while (at != NONE && f->unit[at] < u) {
        before = at;
        at = f->sibling[at];
    }
    if (at != NONE && f->unit[at] == u) { return at; }
    if (f->n_built >= NODES) { return NONE; }
    const uint16_t made = f->n_built++;
    f->child[made] = NONE;
    f->sibling[made] = at;
    f->unit[made] = u;
    if (before == NONE) {
        f->child[node] = made;
    } else {
        f->sibling[before] = made;
    }
    return made;
}

static bool insert(tree_t *f, const uint8_t *units, size_t n)
{
    uint16_t node = 0;
    for (size_t k = 0; k < n && node != NONE; k++) {
        node = child_of(f, node, units[k]);
    }
    return node != NONE;
}

static bool context_id(tree_t *f, uint8_t older, uint8_t newer, uint16_t *id)
{
    for (uint16_t k = 0; k < f->n_contexts; k++) {
        if (f->classes[k][0] == older && f->classes[k][1] == newer) {
            *id = k;
            return true;
        }
    }
    if (f->n_contexts >= CONTEXTS) { return false; }
    f->classes[f->n_contexts][0] = older;
    f->classes[f->n_contexts][1] = newer;
    *id = f->n_contexts++;
    return true;
}

// Breadth first from the root: a node's children get the next numbers, in unit order, after its parent's.
static bool lay_out(tree_t *f)
{
    uint16_t count = 1, arcs = 0;
    f->order[0] = 0;
    f->n_contexts = 0;
    for (uint16_t k = 0; k < count; k++) {
        f->first_arc[k] = arcs;
        for (uint16_t c = f->child[f->order[k]]; c != NONE; c = f->sibling[c]) {
            f->parent[count] = k;
            f->arc_unit[arcs] = f->unit[c];
            f->arc_to[arcs++] = count;
            f->order[count++] = c;
        }
    }
    f->first_arc[count] = arcs;
    f->n_nodes = count;
    for (uint16_t k = 0; k < count; k++) {
        const uint8_t older = k == 0 ? PAD_MARK : f->newer[f->parent[k]];
        f->newer[k] = k == 0 ? BLANK : (uint8_t)(f->unit[f->order[k]] + 1);
        if (!context_id(f, older, f->newer[k], &f->context[k])) { return false; }
    }
    return true;
}

esp_err_t ai_engine_command_rnnt_build(const ai_engine_lexicon_t *lexicon, void *tree, size_t bytes)
{
    if (lexicon == NULL || tree == NULL || bytes < sizeof(tree_t) || lexicon->n_commands == 0 ||
        lexicon->n_commands > AI_ENGINE_COMMANDS_MAX) {
        return ESP_ERR_INVALID_ARG;
    }
    tree_t *f = tree;
    f->n_built = 1;
    f->child[0] = NONE;
    f->sibling[0] = NONE;
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
    return lay_out(f) ? ESP_OK : ESP_ERR_NO_MEM;
}

size_t ai_engine_command_rnnt_work_bytes(size_t n_classes)
{
    return (NODES + (size_t)CONTEXTS * n_classes) * sizeof(float);
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

// Each frame: every distinct context's log-probabilities, each node's probability to its children, parents
// first, then every node's blank.
static esp_err_t forward(const tree_t *f, size_t n_classes, size_t n_frames, uint8_t pad,
                         ai_engine_command_rnnt_log_probs_t log_probs, void *ctx, float *alpha, float *lp)
{
    for (size_t n = 0; n < f->n_nodes; n++) {
        alpha[n] = -INFINITY;
    }
    alpha[0] = 0.0f;
    for (size_t t = 0; t < n_frames; t++) {
        for (size_t k = 0; k < f->n_contexts; k++) {
            const uint8_t context[CONTEXT] = {f->classes[k][0] == PAD_MARK ? pad : f->classes[k][0],
                                              f->classes[k][1]};
            const esp_err_t err = log_probs(ctx, t, context, lp + k * n_classes);
            if (err != ESP_OK) { return err; }
        }
        for (size_t n = 0; n < f->n_nodes; n++) {
            const float source = alpha[n];
            if (source == -INFINITY) { continue; }
            const float *row = lp + (size_t)f->context[n] * n_classes;
            for (uint16_t a = f->first_arc[n]; a < f->first_arc[n + 1]; a++) {
                if ((size_t)f->arc_unit[a] + 1 >= n_classes) { return ESP_ERR_INVALID_SIZE; }
                alpha[f->arc_to[a]] = log_add(alpha[f->arc_to[a]], source + row[f->arc_unit[a] + 1]);
            }
        }
        for (size_t n = 0; n < f->n_nodes; n++) {
            alpha[n] = alpha[n] + lp[(size_t)f->context[n] * n_classes + BLANK];
        }
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
        for (size_t emitted = 0; emitted <= FREE_UNITS; emitted++) {
            const esp_err_t err = log_probs(ctx, t, context, lp);
            if (err != ESP_OK) { return err; }
            size_t best = 0;
            for (size_t c = 1; c < n_classes; c++) {
                if (lp[c] > lp[best]) { best = c; }
            }
            if (best == BLANK || emitted == FREE_UNITS) {
                *total += lp[BLANK];
                break;
            }
            *total += lp[best];
            memmove(context, context + 1, CONTEXT - 1);
            context[CONTEXT - 1] = (uint8_t)best;
        }
    }
    return ESP_OK;
}

static uint16_t node_of(const tree_t *f, const uint8_t *units, size_t n)
{
    uint16_t node = 0;
    for (size_t k = 0; k < n && node != NONE; k++) {
        uint16_t next = NONE;
        for (uint16_t a = f->first_arc[node]; a < f->first_arc[node + 1]; a++) {
            if (f->arc_unit[a] == units[k]) { next = f->arc_to[a]; }
        }
        node = next;
    }
    return node;
}

// Each command's best variant, and best run of whole syllables of a variant short of it, per frame.
static void command_scores(const ai_engine_lexicon_t *lexicon, const tree_t *f, const float *alpha,
                           size_t n_frames, float *best_of, float *part_of)
{
    for (size_t c = 0; c < lexicon->n_commands; c++) {
        for (size_t v = 0; v < lexicon->n_variants[c]; v++) {
            const ai_engine_seq_t *seq = &lexicon->variants[c][v];
            const uint16_t whole = node_of(f, seq->units, seq->n_units);
            if (whole != NONE && alpha[whole] / (float)n_frames > best_of[c]) {
                best_of[c] = alpha[whole] / (float)n_frames;
            }
            size_t ends[UNITS_MAX + 1];
            const size_t n = syllable_ends(seq, ends);
            for (size_t first = 0; first < n; first++) {
                const size_t start = first == 0 ? 0 : ends[first - 1];
                for (size_t last = first; last < n - (first == 0); last++) {
                    const uint16_t run = node_of(f, seq->units + start, ends[last] - start);
                    if (run != NONE && alpha[run] / (float)n_frames > part_of[c]) {
                        part_of[c] = alpha[run] / (float)n_frames;
                    }
                }
            }
        }
    }
}

esp_err_t ai_engine_command_rnnt_decide(const ai_engine_lexicon_t *lexicon, const void *tree,
                                        size_t n_classes, size_t n_frames, uint8_t pad,
                                        ai_engine_command_rnnt_log_probs_t log_probs, void *ctx,
                                        uint16_t reject, uint16_t margin, void *work, float *scores,
                                        ai_engine_command_result_t *out)
{
    if (lexicon == NULL || tree == NULL || log_probs == NULL || work == NULL || out == NULL ||
        n_classes < 2 || lexicon->n_commands == 0 || lexicon->n_commands > AI_ENGINE_COMMANDS_MAX) {
        return ESP_ERR_INVALID_ARG;
    }
    const tree_t *f = tree;
    float *alpha = work;
    float *lp = alpha + NODES;
    float best_of[AI_ENGINE_COMMANDS_MAX], part_of[AI_ENGINE_COMMANDS_MAX];
    for (size_t c = 0; c < lexicon->n_commands; c++) {
        best_of[c] = part_of[c] = -INFINITY;
    }
    if (n_frames > 0) {
        const esp_err_t err = forward(f, n_classes, n_frames, pad, log_probs, ctx, alpha, lp);
        if (err != ESP_OK) { return err; }
        command_scores(lexicon, f, alpha, n_frames, best_of, part_of);
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
