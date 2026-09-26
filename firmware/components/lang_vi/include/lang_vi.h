/** Vietnamese text to recognition units by rule: normalise, g2p, dialect variants (KEHOACH 3.12).
 *  The unit inventory is decided in E11-T3; ids are opaque here and named by lang_vi_unit_name.
 *  @ctx any | non-blocking | no state; output must match ml/src/srpipe/lang exactly
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define LANG_VI_TEXT_MAX_BYTES 256
#define LANG_VI_VARIANTS_MAX 4
#define LANG_VI_UNITS_MAX 48

typedef uint8_t lang_vi_unit_t;

typedef enum {
    LANG_VI_DIALECT_NORTH = 1u << 0,
    LANG_VI_DIALECT_CENTRAL = 1u << 1,
    LANG_VI_DIALECT_SOUTH = 1u << 2,
    LANG_VI_DIALECT_ALL = 0x7u,
} lang_vi_dialect_t;

typedef struct {
    uint8_t n_variants;
    uint8_t n_units[LANG_VI_VARIANTS_MAX];
    lang_vi_unit_t units[LANG_VI_VARIANTS_MAX][LANG_VI_UNITS_MAX];
} lang_vi_pron_t;

/** NFC, lower case, numbers read out, abbreviations and loanwords expanded, one space between syllables.
 *  @ctx any | non-blocking | caller owns out; LANG_VI_TEXT_MAX_BYTES is always enough for 64 characters
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG not UTF-8 | ESP_ERR_INVALID_SIZE out too short
 */
esp_err_t lang_vi_normalize(const char *text, char *out, size_t cap);

/** Units of one normalised utterance as spoken in one dialect.
 *  @ctx any | non-blocking | caller owns units
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG a syllable Vietnamese spelling cannot form | ESP_ERR_INVALID_SIZE
 */
esp_err_t lang_vi_g2p(const char *normalized, lang_vi_dialect_t dialect, lang_vi_unit_t *units, size_t cap,
                      size_t *n_units);

/** Every distinct pronunciation of a command line across the dialects in mask, normalising first.
 *  @ctx any | non-blocking
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE longer than LANG_VI_UNITS_MAX
 */
esp_err_t lang_vi_lexicon_entry(const char *text, lang_vi_dialect_t mask, lang_vi_pron_t *out);

/** Printable name of a unit, for logs and golden files.
 *  @ctx any | non-blocking | returns a static string, "?" for an unknown id
 */
const char *lang_vi_unit_name(lang_vi_unit_t unit);

#ifdef __cplusplus
}
#endif
