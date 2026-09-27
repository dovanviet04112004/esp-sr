#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "gen_lang_vi.h"
#include "lang_internal.h"
#include "lang_vi.h"

#define UNKNOWN_UNIT_NAME "?"
// Longer than any syllable spelling can be; a longer token is refused as no syllable.
#define BASE_MAX_BYTES 32
#define UNITS_PER_SYLLABLE 5
#define ONSET_QU "qu"
#define ONSET_GI "gi"

typedef struct {
    const char *onset; // "" without an onset
    const gen_lang_vi_rhyme_t *rhyme;
    uint8_t tone;
} spelling_t;

static bool in_units(uint8_t unit, const uint8_t *units, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        if (units[i] == unit) { return true; }
    }
    return false;
}

static bool in_codepoints(uint32_t cp, const uint32_t *list, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        if (list[i] == cp) { return true; }
    }
    return false;
}

// *to takes the rewrite of unit when pairs has one, and stays as it is otherwise.
static void rewrite(uint8_t unit, const gen_lang_vi_pair_t *pairs, size_t n, uint8_t *to)
{
    for (size_t i = 0; i < n; i++) {
        if (pairs[i].from == unit) { *to = pairs[i].to; }
    }
}

// The base letters of a lower-case syllable [text, end) as UTF-8, and its one tone wherever the mark sits.
static bool split_tone(const char *text, const char *end, char *base, uint8_t *tone)
{
    size_t len = 0;
    *tone = 0;
    for (const char *p = text; p < end;) {
        const uint32_t cp = lang_next_codepoint(&p);
        uint32_t letter = cp;
        uint8_t letter_tone = 0;
        const bool ascii = cp >= 'a' && cp <= 'z';
        if (!ascii &&
            (!lang_letter_of(cp, &letter, &letter_tone) || lang_letter_form(letter, letter_tone) != cp)) {
            return false;
        }
        if (letter_tone != 0 && *tone != 0) { return false; }
        *tone = letter_tone != 0 ? letter_tone : *tone;
        if (!lang_put_codepoint(base, BASE_MAX_BYTES, &len, letter)) { return false; }
    }
    return true;
}

static const char *longest_onset(const char *base)
{
    for (size_t i = 0; i < GEN_LANG_VI_N_ONSETS; i++) {
        const char *onset = GEN_LANG_VI_ONSETS[i].spelling;
        if (strncmp(base, onset, strlen(onset)) == 0) { return onset; }
    }
    return "";
}

static const gen_lang_vi_rhyme_t *find_rhyme(const gen_lang_vi_rhyme_t *table, size_t n, const char *key)
{
    for (size_t i = 0; i < n; i++) {
        if (strcmp(table[i].spelling, key) == 0) { return &table[i]; }
    }
    return NULL;
}

static uint32_t first_codepoint(const char *text)
{
    return *text == '\0' ? 0 : lang_next_codepoint(&text);
}

// The table key of the written rhyme: after gi, the i gi shares with the rhyme comes back.
static bool rhyme_key(const char *onset, const char *rest, char *key)
{
    const uint32_t first = first_codepoint(rest);
    if (strcmp(onset, ONSET_GI) != 0) {
        strcpy(key, rest);
        return true;
    }
    if (in_codepoints(first, GEN_LANG_VI_GI_NEVER_BEFORE, GEN_LANG_VI_N_GI_NEVER_BEFORE)) { return false; }
    const bool shares = first == 0 || !lang_is_vowel_base(first) ||
                        in_codepoints(first, GEN_LANG_VI_GI_SHARES_BEFORE, GEN_LANG_VI_N_GI_SHARES_BEFORE);
    key[0] = '\0';
    if (shares) { strcpy(key, "i"); }
    strcat(key, rest);
    return true;
}

static bool spelling_rules_hold(const char *onset, uint32_t first)
{
    for (size_t i = 0; i < GEN_LANG_VI_N_SPELLING_RULES; i++) {
        const gen_lang_vi_spelling_rule_t *rule = &GEN_LANG_VI_SPELLING_RULES[i];
        if (strcmp(rule->onset, onset) == 0 &&
            in_codepoints(first, rule->letters, rule->n_letters) != rule->only) {
            return false;
        }
    }
    return true;
}

static bool takes_no_glide(const char *onset)
{
    for (size_t i = 0; i < GEN_LANG_VI_N_NO_GLIDE; i++) {
        if (strcmp(GEN_LANG_VI_NO_GLIDE[i], onset) == 0) { return true; }
    }
    return false;
}

// Parses one syllable by Vietnamese spelling; false where the spelling cannot build it (KEHOACH 3.12).
static bool parse(const char *text, const char *end, spelling_t *out)
{
    char base[BASE_MAX_BYTES];
    char key[BASE_MAX_BYTES + 1];
    if (!split_tone(text, end, base, &out->tone)) { return false; }
    out->onset = longest_onset(base);
    const char *rest = base + strlen(out->onset);
    if (!rhyme_key(out->onset, rest, key)) { return false; }
    const bool after_qu = strcmp(out->onset, ONSET_QU) == 0;
    out->rhyme = after_qu ? find_rhyme(GEN_LANG_VI_Q_RHYMES, GEN_LANG_VI_N_Q_RHYMES, key)
                          : find_rhyme(GEN_LANG_VI_RHYMES, GEN_LANG_VI_N_RHYMES, key);
    if (out->rhyme == NULL) { return false; }
    const bool has_onset = out->onset[0] != '\0';
    const uint8_t tone_unit = GEN_LANG_VI_TONE_UNITS[out->tone];
    if (out->rhyme->glide != GEN_LANG_VI_UNIT_NONE && takes_no_glide(out->onset)) { return false; }
    if (!spelling_rules_hold(out->onset, first_codepoint(rest))) { return false; }
    if ((out->rhyme->flags & GEN_LANG_VI_RHYME_ZERO_ONSET_ONLY) != 0 && has_onset) { return false; }
    if ((out->rhyme->flags & GEN_LANG_VI_RHYME_ONSET_ONLY) != 0 && !has_onset) { return false; }
    return !in_units(out->rhyme->coda, GEN_LANG_VI_CHECKED_CODAS, GEN_LANG_VI_N_CHECKED_CODAS) ||
           in_units(tone_unit, GEN_LANG_VI_CHECKED_TONES, GEN_LANG_VI_N_CHECKED_TONES);
}

static uint8_t onset_unit(const char *onset, size_t dialect)
{
    for (size_t i = 0; i < GEN_LANG_VI_N_ONSETS; i++) {
        if (strcmp(GEN_LANG_VI_ONSETS[i].spelling, onset) == 0) {
            return GEN_LANG_VI_ONSETS[i].units[dialect];
        }
    }
    return GEN_LANG_VI_UNIT_NONE;
}

// The units a region speaks for a spelling, each rewrite looking at the nucleus and coda as spelled.
static size_t read_units(const spelling_t *s, size_t dialect, uint8_t *units)
{
    const gen_lang_vi_dialect_rules_t *r = &GEN_LANG_VI_DIALECT_RULES[dialect];
    const uint8_t glide = s->rhyme->glide, nucleus = s->rhyme->nucleus, coda = s->rhyme->coda;
    uint8_t spoken[UNITS_PER_SYLLABLE] = {onset_unit(s->onset, dialect), glide, nucleus, coda,
                                          GEN_LANG_VI_TONE_UNITS[s->tone]};
    uint8_t *onset = &spoken[0], *spoken_nucleus = &spoken[2], *spoken_coda = &spoken[3], *tone = &spoken[4];
    if (glide != GEN_LANG_VI_UNIT_NONE && in_units(*onset, r->glide_drops_onset, r->n_glide_drops_onset)) {
        *onset = GEN_LANG_VI_UNIT_NONE;
    }
    if (!in_units(nucleus, r->velar_keep_after, r->n_velar_keep_after)) {
        rewrite(coda, r->velar_codas, r->n_velar_codas, spoken_coda);
    }
    rewrite(coda, r->palatal_codas, r->n_palatal_codas, spoken_coda);
    if (in_units(coda, r->centralize_before, r->n_centralize_before)) {
        rewrite(nucleus, r->centralize, r->n_centralize, spoken_nucleus);
    }
    rewrite(*tone, r->tone, r->n_tone, tone);
    size_t n = 0;
    for (size_t i = 0; i < UNITS_PER_SYLLABLE; i++) {
        if (spoken[i] != GEN_LANG_VI_UNIT_NONE) { units[n++] = spoken[i]; }
    }
    return n;
}

static int dialect_index(lang_vi_dialect_t dialect)
{
    for (int d = 0; d < GEN_LANG_VI_N_DIALECTS; d++) {
        if ((unsigned)dialect == 1u << d) { return d; }
    }
    return -1;
}

esp_err_t lang_vi_g2p(const char *normalized, lang_vi_dialect_t dialect, lang_vi_unit_t *units, size_t cap,
                      size_t *n_units)
{
    const int d = dialect_index(dialect);
    if (n_units != NULL) { *n_units = 0; }
    if (normalized == NULL || n_units == NULL || (units == NULL && cap > 0) || d < 0 ||
        !lang_is_utf8(normalized)) {
        return ESP_ERR_INVALID_ARG;
    }
    size_t n = 0;
    for (const char *p = normalized; *p != '\0';) {
        if (*p == ' ') {
            p++;
            continue;
        }
        const char *end = strchr(p, ' ');
        end = end != NULL ? end : p + strlen(p);
        spelling_t spelling;
        uint8_t spoken[UNITS_PER_SYLLABLE];
        if (!parse(p, end, &spelling)) { return ESP_ERR_INVALID_ARG; }
        const size_t k = read_units(&spelling, (size_t)d, spoken);
        if (n + k > cap) { return ESP_ERR_INVALID_SIZE; }
        memcpy(units + n, spoken, k);
        n += k;
        p = end;
    }
    *n_units = n;
    return ESP_OK;
}

const char *lang_vi_unit_name(lang_vi_unit_t unit)
{
    return unit < GEN_LANG_VI_N_UNITS ? GEN_LANG_VI_UNIT_NAMES[unit] : UNKNOWN_UNIT_NAME;
}
