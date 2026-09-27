#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "gen_lang_vi.h"
#include "lang_internal.h"
#include "lang_vi.h"

#define CODEPOINT_MAX 0x10FFFFu
#define SURROGATE_FIRST 0xD800u
#define SURROGATE_LAST 0xDFFFu
#define COMBINING_FIRST 0x0300u
#define COMBINING_LAST 0x036Fu
#define ITEM_CODEPOINT_MASK 0x1FFFFFu
#define ITEM_TONE_SHIFT 21
#define ITEM_TONE_MASK 0x7u
#define ITEM_LETTER_BIT (1u << 24)
#define GROUP_BASE 1000u
#define N_GROUPS 4

// One code point after composition: base letter, tone and a letter flag packed so 256 items take 1 KB of
// stack.
typedef uint32_t item_t;

typedef enum { KIND_LETTER, KIND_DIGIT, KIND_SPACE, KIND_SYMBOL, KIND_OTHER } kind_t;

typedef struct {
    char *out;
    size_t cap;
    size_t len;
    size_t last_word;
    bool full; // stays set: the result is ESP_ERR_INVALID_SIZE
} writer_t;

uint32_t lang_next_codepoint(const char **p)
{
    static const uint32_t kMinOfLength[] = {0, 0, 0x80u, 0x800u, 0x10000u};
    const uint8_t *s = (const uint8_t *)*p;
    const size_t length = s[0] < 0x80u           ? 1
                          : (s[0] >> 5) == 0x6u  ? 2
                          : (s[0] >> 4) == 0xEu  ? 3
                          : (s[0] >> 3) == 0x1Eu ? 4
                                                 : 0;
    if (length == 0) { return LANG_CODEPOINT_BAD; }
    uint32_t cp = length == 1 ? s[0] : s[0] & (0x7Fu >> length);
    for (size_t i = 1; i < length; i++) {
        if ((s[i] & 0xC0u) != 0x80u) { return LANG_CODEPOINT_BAD; }
        cp = (cp << 6) | (s[i] & 0x3Fu);
    }
    if (length > 1 && (cp < kMinOfLength[length] || cp > CODEPOINT_MAX ||
                       (cp >= SURROGATE_FIRST && cp <= SURROGATE_LAST))) {
        return LANG_CODEPOINT_BAD;
    }
    *p += length;
    return cp;
}

bool lang_is_utf8(const char *text)
{
    for (const char *p = text; *p != '\0';) {
        if (lang_next_codepoint(&p) == LANG_CODEPOINT_BAD) { return false; }
    }
    return true;
}

bool lang_letter_of(uint32_t codepoint, uint32_t *base, uint8_t *tone)
{
    for (size_t i = 0; i < GEN_LANG_VI_N_LETTERS; i++) {
        if (GEN_LANG_VI_LETTERS[i].codepoint == codepoint) {
            *base = GEN_LANG_VI_LETTERS[i].base;
            *tone = GEN_LANG_VI_LETTERS[i].tone;
            return true;
        }
    }
    return false;
}

bool lang_is_vowel_base(uint32_t base)
{
    for (size_t v = 0; v < GEN_LANG_VI_N_VOWELS; v++) {
        if (GEN_LANG_VI_VOWEL_FORMS[v][0] == base) { return true; }
    }
    return false;
}

uint32_t lang_letter_form(uint32_t base, uint8_t tone)
{
    for (size_t v = 0; v < GEN_LANG_VI_N_VOWELS; v++) {
        if (GEN_LANG_VI_VOWEL_FORMS[v][0] == base) { return GEN_LANG_VI_VOWEL_FORMS[v][tone]; }
    }
    return base;
}

static size_t utf8_bytes(uint32_t cp)
{
    return cp < 0x80u ? 1 : cp < 0x800u ? 2 : cp < 0x10000u ? 3 : 4;
}

bool lang_put_codepoint(char *out, size_t cap, size_t *len, uint32_t codepoint)
{
    const size_t n = utf8_bytes(codepoint);
    if (*len + n + 1 > cap) { return false; }
    uint8_t *d = (uint8_t *)out + *len;
    if (n == 1) {
        d[0] = (uint8_t)codepoint;
    } else {
        for (size_t i = n - 1; i > 0; i--) {
            d[i] = (uint8_t)(0x80u | (codepoint & 0x3Fu));
            codepoint >>= 6;
        }
        d[0] = (uint8_t)((0xF00u >> n) | codepoint);
    }
    *len += n;
    out[*len] = '\0';
    return true;
}

static item_t letter_item(uint32_t base, uint8_t tone)
{
    return ITEM_LETTER_BIT | ((uint32_t)tone << ITEM_TONE_SHIFT) | base;
}

static uint32_t item_codepoint(item_t item)
{
    return item & ITEM_CODEPOINT_MASK;
}

static uint8_t item_tone(item_t item)
{
    return (uint8_t)((item >> ITEM_TONE_SHIFT) & ITEM_TONE_MASK);
}

static bool item_is_letter(item_t item)
{
    return (item & ITEM_LETTER_BIT) != 0;
}

// The rendered code point: a letter in its tone form.
static uint32_t item_text(item_t item)
{
    return item_is_letter(item) ? lang_letter_form(item_codepoint(item), item_tone(item))
                                : item_codepoint(item);
}

static bool tone_of_mark(uint32_t cp, uint8_t *tone)
{
    for (size_t i = 0; i < GEN_LANG_VI_N_TONE_MARKS; i++) {
        if (GEN_LANG_VI_TONE_MARKS[i].mark == cp) {
            *tone = GEN_LANG_VI_TONE_MARKS[i].tone;
            return true;
        }
    }
    return false;
}

static bool modified(uint32_t mark, uint32_t base, uint32_t *to)
{
    for (size_t i = 0; i < GEN_LANG_VI_N_MODIFIERS; i++) {
        if (GEN_LANG_VI_MODIFIERS[i].mark == mark && GEN_LANG_VI_MODIFIERS[i].from == base) {
            *to = GEN_LANG_VI_MODIFIERS[i].to;
            return true;
        }
    }
    return false;
}

// Marks may follow their letter in any order: NFD puts the dot below of ặ ahead of its breve.
static esp_err_t compose(const char *text, item_t *items, size_t *n)
{
    *n = 0;
    for (const char *p = text; *p != '\0';) {
        const uint32_t cp = lang_next_codepoint(&p);
        item_t *last = *n > 0 && item_is_letter(items[*n - 1]) ? &items[*n - 1] : NULL;
        uint32_t base = 0;
        uint8_t tone = 0;
        item_t next = cp;
        if (lang_letter_of(cp, &base, &tone)) {
            next = letter_item(base, tone);
        } else if ((cp >= 'A' && cp <= 'Z') || (cp >= 'a' && cp <= 'z')) {
            next = letter_item(cp | 0x20u, 0);
        } else if (last != NULL && tone_of_mark(cp, &tone) && lang_is_vowel_base(item_codepoint(*last)) &&
                   item_tone(*last) == 0) {
            *last = letter_item(item_codepoint(*last), tone);
            continue;
        } else if (last != NULL && modified(cp, item_codepoint(*last), &base)) {
            *last = letter_item(base, item_tone(*last));
            continue;
        }
        if (*n == LANG_VI_TEXT_MAX_BYTES) { return ESP_ERR_INVALID_SIZE; }
        items[(*n)++] = next;
    }
    return ESP_OK;
}

static const char *symbol_text(uint32_t cp)
{
    for (size_t i = 0; i < GEN_LANG_VI_N_SYMBOLS; i++) {
        if (GEN_LANG_VI_SYMBOLS[i].codepoint == cp) { return GEN_LANG_VI_SYMBOLS[i].text; }
    }
    return NULL;
}

// A combining mark nothing composed with stays in its word, so g2p refuses the word.
static kind_t kind_of(item_t item)
{
    const uint32_t cp = item_codepoint(item);
    if (item_is_letter(item) || (cp >= COMBINING_FIRST && cp <= COMBINING_LAST)) { return KIND_LETTER; }
    if (cp >= '0' && cp <= '9') { return KIND_DIGIT; }
    if (cp == ' ' || (cp >= '\t' && cp <= '\r')) { return KIND_SPACE; }
    return symbol_text(cp) != NULL ? KIND_SYMBOL : KIND_OTHER;
}

// items [lo, hi) rendered into key; false when they do not fit, so they match no dictionary key.
static bool render(const item_t *items, size_t lo, size_t hi, char *key, size_t cap)
{
    size_t len = 0;
    key[0] = '\0';
    for (size_t i = lo; i < hi; i++) {
        if (!lang_put_codepoint(key, cap, &len, item_text(items[i]))) { return false; }
    }
    return true;
}

static const char *dictionary(const item_t *items, size_t lo, size_t hi)
{
    char key[GEN_LANG_VI_DICTIONARY_KEY_MAX_BYTES + 1];
    if (!render(items, lo, hi, key, sizeof(key))) { return NULL; }
    for (size_t i = 0; i < GEN_LANG_VI_N_DICTIONARY; i++) {
        if (strcmp(key, GEN_LANG_VI_DICTIONARY[i].key) == 0) { return GEN_LANG_VI_DICTIONARY[i].text; }
    }
    return NULL;
}

// Room for a separating space, bytes and a NUL, or the writer turns full for good.
static bool make_room(writer_t *w, size_t bytes)
{
    const size_t separator = w->len > 0 ? 1 : 0;
    if (w->full || w->len + separator + bytes + 1 > w->cap) {
        w->full = true;
        return false;
    }
    if (separator > 0) { w->out[w->len++] = ' '; }
    return true;
}

static void emit(writer_t *w, const char *text)
{
    const size_t bytes = strlen(text);
    if (!make_room(w, bytes)) { return; }
    const char *space = strrchr(text, ' ');
    w->last_word = w->len + (space != NULL ? (size_t)(space - text) + 1 : 0);
    memcpy(w->out + w->len, text, bytes + 1);
    w->len += bytes;
}

static void emit_items(writer_t *w, const item_t *items, size_t lo, size_t hi)
{
    size_t bytes = 0;
    for (size_t i = lo; i < hi; i++) {
        bytes += utf8_bytes(item_text(items[i]));
    }
    if (!make_room(w, bytes)) { return; }
    w->last_word = w->len;
    for (size_t i = lo; i < hi; i++) {
        lang_put_codepoint(w->out, w->cap, &w->len, item_text(items[i]));
    }
}

static bool last_word_is(const writer_t *w, const char *word)
{
    const size_t bytes = strlen(word);
    return !w->full && w->len > 0 && w->len - w->last_word == bytes &&
           memcmp(w->out + w->last_word, word, bytes) == 0;
}

static const char *unit_after_tens(unsigned tens, unsigned unit)
{
    if (unit == 5) { return GEN_LANG_VI_NUMBER_FIVE_AFTER_TEN; }
    if (tens > 1 && unit == 1) { return GEN_LANG_VI_NUMBER_ONE_AFTER_TENS; }
    if (tens > 1 && unit == 4) { return GEN_LANG_VI_NUMBER_FOUR_AFTER_TENS; }
    return GEN_LANG_VI_NUMBER_DIGITS[unit];
}

// One group of three digits; full says "không trăm" and "linh" where a leading group would not.
static void emit_hundreds(writer_t *w, unsigned group, bool full, size_t dialect)
{
    const unsigned h = group / 100, t = group / 10 % 10, u = group % 10;
    const bool hundred_said = full || h > 0;
    if (hundred_said) {
        emit(w, GEN_LANG_VI_NUMBER_DIGITS[h]);
        emit(w, GEN_LANG_VI_NUMBER_HUNDRED);
    }
    if (t == 0) {
        if (u > 0 && hundred_said) { emit(w, GEN_LANG_VI_NUMBER_ZERO_TENS[dialect]); }
        if (u > 0) { emit(w, GEN_LANG_VI_NUMBER_DIGITS[u]); }
        return;
    }
    if (t == 1) {
        emit(w, GEN_LANG_VI_NUMBER_TEN);
    } else {
        emit(w, GEN_LANG_VI_NUMBER_DIGITS[t]);
        emit(w, GEN_LANG_VI_NUMBER_TENS);
    }
    if (u > 0) { emit(w, unit_after_tens(t, u)); }
}

static void emit_number(writer_t *w, uint64_t value, size_t dialect)
{
    const char *names[N_GROUPS] = {NULL, GEN_LANG_VI_NUMBER_THOUSAND[dialect], GEN_LANG_VI_NUMBER_MILLION,
                                   GEN_LANG_VI_NUMBER_BILLION};
    if (value == 0) {
        emit(w, GEN_LANG_VI_NUMBER_DIGITS[0]);
        return;
    }
    uint64_t scale = (uint64_t)GROUP_BASE * GROUP_BASE * GROUP_BASE;
    bool said = false;
    for (int i = N_GROUPS - 1; i >= 0; i--, scale /= GROUP_BASE) {
        const unsigned group = (unsigned)(value / scale % GROUP_BASE);
        if (group == 0) { continue; }
        emit_hundreds(w, group, said, dialect);
        if (names[i] != NULL) { emit(w, names[i]); }
        said = true;
    }
}

// A run of digits: a number up to the contract's digit limit without a leading 0, else digit by digit.
static void emit_integer(writer_t *w, const char *digits, size_t len, size_t dialect)
{
    if (len > GEN_LANG_VI_NUMBER_MAX_DIGITS || (len > 1 && digits[0] == '0')) {
        for (size_t i = 0; i < len; i++) {
            emit(w, GEN_LANG_VI_NUMBER_DIGITS[digits[i] - '0']);
        }
        return;
    }
    uint64_t value = 0;
    for (size_t i = 0; i < len; i++) {
        value = value * 10 + (uint64_t)(digits[i] - '0');
    }
    emit_number(w, value, dialect);
}

static bool is_digit(char c)
{
    return c >= '0' && c <= '9';
}

// 1 to 3 digits, then groups of a dot and 3 digits.
static bool is_thousands(const char *text, size_t len)
{
    size_t lead = 0;
    while (lead < len && is_digit(text[lead])) {
        lead++;
    }
    if (lead == 0 || lead > 3 || lead == len || (len - lead) % 4 != 0) { return false; }
    for (size_t i = lead; i < len; i++) {
        if ((i - lead) % 4 == 0 ? text[i] != '.' : !is_digit(text[i])) { return false; }
    }
    return true;
}

static bool ordinal(const writer_t *w, const char *text, size_t len, const char **word)
{
    if (len > GEN_LANG_VI_NUMBER_MAX_DIGITS || text[0] == '0' ||
        !last_word_is(w, GEN_LANG_VI_NUMBER_ORDINAL_WORD)) {
        return false;
    }
    uint64_t value = 0;
    for (size_t i = 0; i < len; i++) {
        if (!is_digit(text[i])) { return false; }
        value = value * 10 + (uint64_t)(text[i] - '0');
    }
    for (size_t i = 0; i < GEN_LANG_VI_N_ORDINALS; i++) {
        if (GEN_LANG_VI_ORDINALS[i].value == value) {
            *word = GEN_LANG_VI_ORDINALS[i].word;
            return true;
        }
    }
    return false;
}

// Digits with . and , between them: thousands groups, else each piece read out with chấm or phẩy.
static void emit_number_part(writer_t *w, const item_t *items, size_t lo, size_t hi, size_t dialect)
{
    char text[LANG_VI_TEXT_MAX_BYTES + 1];
    const size_t len = hi - lo;
    for (size_t i = 0; i < len; i++) {
        text[i] = (char)item_codepoint(items[lo + i]);
    }
    const char *word = NULL;
    if (is_thousands(text, len)) {
        size_t n = 0;
        for (size_t i = 0; i < len; i++) {
            if (text[i] != '.') { text[n++] = text[i]; }
        }
        emit_integer(w, text, n, dialect);
    } else if (ordinal(w, text, len, &word)) {
        emit(w, word);
    } else {
        size_t start = 0;
        for (size_t i = 0; i <= len; i++) {
            if (i < len && text[i] != '.' && text[i] != ',') { continue; }
            emit_integer(w, text + start, i - start, dialect);
            if (i < len) {
                emit(w, text[i] == ',' ? GEN_LANG_VI_NUMBER_DECIMAL_COMMA : GEN_LANG_VI_NUMBER_DOT);
            }
            start = i + 1;
        }
    }
}

static void read_run(writer_t *w, const item_t *items, size_t lo, size_t hi, size_t dialect)
{
    if (hi - lo == 1 && kind_of(items[lo]) == KIND_SYMBOL) {
        emit(w, symbol_text(item_codepoint(items[lo])));
        return;
    }
    const char *entry = dictionary(items, lo, hi);
    if (entry != NULL) {
        emit(w, entry);
        return;
    }
    for (size_t a = lo; a < hi;) {
        const bool letters = kind_of(items[a]) == KIND_LETTER;
        size_t b = a;
        while (b < hi && (kind_of(items[b]) == KIND_LETTER) == letters) {
            b++;
        }
        entry = letters ? dictionary(items, a, b) : NULL;
        if (entry != NULL) {
            emit(w, entry);
        } else if (letters) {
            emit_items(w, items, a, b);
        } else {
            emit_number_part(w, items, a, b, dialect);
        }
        a = b;
    }
}

static bool joins_digits(const item_t *items, size_t i, size_t lo, size_t hi)
{
    const uint32_t cp = item_codepoint(items[i]);
    return (cp == '.' || cp == ',') && i > lo && kind_of(items[i - 1]) == KIND_DIGIT && i + 1 < hi &&
           kind_of(items[i + 1]) == KIND_DIGIT;
}

static void read_chunk(writer_t *w, const item_t *items, size_t lo, size_t hi, size_t dialect)
{
    while (lo < hi && kind_of(items[lo]) == KIND_OTHER) {
        lo++;
    }
    while (hi > lo && kind_of(items[hi - 1]) == KIND_OTHER) {
        hi--;
    }
    const char *entry = dictionary(items, lo, hi);
    if (entry != NULL) {
        emit(w, entry);
        return;
    }
    for (size_t i = lo; i < hi;) {
        const kind_t kind = kind_of(items[i]);
        if (kind == KIND_SYMBOL) {
            read_run(w, items, i, i + 1, dialect);
            i++;
            continue;
        }
        if (kind != KIND_LETTER && kind != KIND_DIGIT) {
            i++;
            continue;
        }
        size_t j = i;
        while (j < hi && (kind_of(items[j]) == KIND_LETTER || kind_of(items[j]) == KIND_DIGIT ||
                          joins_digits(items, j, i, hi))) {
            j++;
        }
        read_run(w, items, i, j, dialect);
        i = j;
    }
}

esp_err_t lang_normalize_for(const char *text, size_t dialect, char *out, size_t cap)
{
    if (text == NULL || out == NULL || dialect >= GEN_LANG_VI_N_DIALECTS || !lang_is_utf8(text)) {
        return ESP_ERR_INVALID_ARG;
    }
    item_t items[LANG_VI_TEXT_MAX_BYTES];
    size_t n = 0;
    const esp_err_t err = compose(text, items, &n);
    if (err != ESP_OK) { return err; }
    if (cap == 0) { return ESP_ERR_INVALID_SIZE; }
    writer_t w = {.out = out, .cap = cap};
    out[0] = '\0';
    for (size_t i = 0; i < n;) {
        if (kind_of(items[i]) == KIND_SPACE) {
            i++;
            continue;
        }
        size_t j = i;
        while (j < n && kind_of(items[j]) != KIND_SPACE) {
            j++;
        }
        read_chunk(&w, items, i, j, dialect);
        i = j;
    }
    return w.full ? ESP_ERR_INVALID_SIZE : ESP_OK;
}

esp_err_t lang_vi_normalize(const char *text, char *out, size_t cap)
{
    return lang_normalize_for(text, 0, out, cap);
}
