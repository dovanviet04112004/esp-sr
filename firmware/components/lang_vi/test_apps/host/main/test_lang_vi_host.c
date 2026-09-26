#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "lang_vi.h"

static unsigned s_failures;

static void check(bool ok, const char *what)
{
    printf("HOST %s: %s\n", ok ? "PASS" : "FAIL", what);
    s_failures += ok ? 0u : 1u;
}

static void check_normalize(void)
{
    char out[LANG_VI_TEXT_MAX_BYTES];
    char tiny[4];
    const char *text = "b\xe1\xba\xadt \xc4\x91\xc3\xa8n"; // "bật đèn"
    check(lang_vi_normalize(text, out, sizeof(out)) == ESP_OK && strcmp(out, text) == 0,
          "normalize shell: well-formed UTF-8 comes back as it is");
    check(lang_vi_normalize(text, tiny, sizeof(tiny)) == ESP_ERR_INVALID_SIZE,
          "normalize: a short buffer is refused");
    const char *bad[] = {"\xc3", "\xc0\xaf", "\xed\xa0\x80", "\xf4\x90\x80\x80", "\xff"};
    bool refused = true;
    for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        refused = refused && lang_vi_normalize(bad[i], out, sizeof(out)) == ESP_ERR_INVALID_ARG;
    }
    check(refused, "normalize: truncated, overlong, surrogate, past U+10FFFF and stray bytes are not UTF-8");
}

static void check_g2p_and_lexicon(void)
{
    lang_vi_unit_t units[LANG_VI_UNITS_MAX];
    size_t n_units = 7;
    lang_vi_pron_t pron;
    memset(&pron, 0xA5, sizeof(pron));
    check(lang_vi_g2p("bat den", LANG_VI_DIALECT_NORTH, units, LANG_VI_UNITS_MAX, &n_units) == ESP_OK &&
              n_units == 0,
          "g2p shell: no units yet");
    check(lang_vi_g2p("bat den", LANG_VI_DIALECT_ALL, units, LANG_VI_UNITS_MAX, &n_units) ==
              ESP_ERR_INVALID_ARG,
          "g2p: more than one dialect is refused");
    check(lang_vi_lexicon_entry("b\xe1\xba\xadt \xc4\x91\xc3\xa8n", LANG_VI_DIALECT_ALL, &pron) == ESP_OK &&
              pron.n_variants == 0,
          "lexicon shell: no pronunciation yet");
    check(lang_vi_lexicon_entry("\xc0\xaf", LANG_VI_DIALECT_ALL, &pron) == ESP_ERR_INVALID_ARG &&
              lang_vi_lexicon_entry("bat", 0, &pron) == ESP_ERR_INVALID_ARG,
          "lexicon: normalises first, and needs at least one dialect");
    check(strcmp(lang_vi_unit_name(3), "?") == 0, "unit_name: every id is unknown yet");
}

int main(void)
{
    check_normalize();
    check_g2p_and_lexicon();
    printf("HOST %u failure(s)\n", s_failures);
    return s_failures == 0 ? 0 : 1;
}
