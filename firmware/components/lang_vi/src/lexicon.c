#include <string.h>

#include "lang_vi.h"

// Neutral shell of E3-T4: every entry has no pronunciation until E11-T4 (KEHOACH 3.12).

esp_err_t lang_vi_lexicon_entry(const char *text, lang_vi_dialect_t mask, lang_vi_pron_t *out)
{
    if (text == NULL || out == NULL || mask == 0 || (mask & ~LANG_VI_DIALECT_ALL) != 0) {
        return ESP_ERR_INVALID_ARG;
    }
    char normalized[LANG_VI_TEXT_MAX_BYTES];
    const esp_err_t err = lang_vi_normalize(text, normalized, sizeof(normalized));
    if (err != ESP_OK) { return err; }
    memset(out, 0, sizeof(*out));
    return ESP_OK;
}
