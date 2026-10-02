/** What the command parity cases share: the decision row, the stand-in for -inf, and the packed lexicon.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "ai_engine.h"
#include "gold_read.h"

enum {
    PARITY_DECISION_COMMAND = 0,
    PARITY_DECISION_SCORE,
    PARITY_DECISION_MARGIN,
    PARITY_DECISION_GAP,
    PARITY_DECISION_COUNT
};

/** A command decision as the four floats a golden decision row holds, in DECISION's order.
 *  @ctx any | non-blocking
 */
void parity_decision_row(const ai_engine_command_result_t *d, float *row);

/** Each -inf of x as a finite stand-in the comparators can difference.
 *  @ctx any | non-blocking
 */
void parity_mark_unreached(float *x, size_t n);

/** The lexicon a command case packs: units (commands, variants, longest), their counts; ids keeps the units.
 *  @ctx any | non-blocking | caller owns lex and ids, commands x variants x longest bytes
 *  @ret false when the case breaks the lexicon limits or memory runs out
 */
bool parity_lexicon_of(const gold_tensor_t *units, const gold_tensor_t *n_variants,
                       const gold_tensor_t *n_units, ai_engine_lexicon_t *lex, uint8_t *ids);
