#include <assert.h>
#include <stdbool.h>
#include <string.h>

#include "gen_lang_vi.h"
#include "lang_internal.h"
#include "lang_vi.h"

static_assert(GEN_LANG_VI_N_DIALECTS <= LANG_VI_VARIANTS_MAX,
              "one variant per dialect must fit lang_vi_pron_t");
static_assert(GEN_LANG_VI_N_UNITS < GEN_LANG_VI_UNIT_NONE, "unit ids fit lang_vi_unit_t below the pad value");

static bool already_there(const lang_vi_pron_t *pron, const lang_vi_unit_t *units, size_t n)
{
    for (size_t v = 0; v < pron->n_variants; v++) {
        if (pron->n_units[v] == n && memcmp(pron->units[v], units, n) == 0) { return true; }
    }
    return false;
}

// Each dialect normalises the line its own way, since number words differ by region (KEHOACH 3.12).
esp_err_t lang_vi_lexicon_entry(const char *text, lang_vi_dialect_t mask, lang_vi_pron_t *out)
{
    if (text == NULL || out == NULL || mask == 0 || (mask & ~LANG_VI_DIALECT_ALL) != 0) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(out, 0, sizeof(*out));
    char normalized[LANG_VI_TEXT_MAX_BYTES];
    lang_vi_unit_t units[LANG_VI_UNITS_MAX];
    for (size_t d = 0; d < GEN_LANG_VI_N_DIALECTS; d++) {
        if ((mask & (1u << d)) == 0) { continue; }
        size_t n = 0;
        esp_err_t err = lang_normalize_for(text, d, normalized, sizeof(normalized));
        if (err == ESP_OK) {
            err = lang_vi_g2p(normalized, (lang_vi_dialect_t)(1u << d), units, LANG_VI_UNITS_MAX, &n);
        }
        if (err == ESP_OK && n == 0) { err = ESP_ERR_INVALID_ARG; }
        if (err != ESP_OK) {
            memset(out, 0, sizeof(*out));
            return err;
        }
        if (!already_there(out, units, n)) {
            memcpy(out->units[out->n_variants], units, n);
            out->n_units[out->n_variants++] = (uint8_t)n;
        }
    }
    return ESP_OK;
}
