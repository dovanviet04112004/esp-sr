// GENERATED FILE - DO NOT EDIT.
// Source: contracts/lang_vi.yaml
// Regenerate: python3 tools/gen_contracts.py

#pragma once

#include <stdbool.h>
#include <stdint.h>

#define GEN_LANG_VI_VERSION 1
#define GEN_LANG_VI_N_UNITS 44
#define GEN_LANG_VI_N_DIALECTS 3
#define GEN_LANG_VI_N_TONES 6
#define GEN_LANG_VI_N_VOWELS 12
#define GEN_LANG_VI_N_LETTERS 146
#define GEN_LANG_VI_N_TONE_MARKS 7
#define GEN_LANG_VI_N_MODIFIERS 6
#define GEN_LANG_VI_N_ONSETS 27
#define GEN_LANG_VI_N_SPELLING_RULES 6
#define GEN_LANG_VI_FRONT_MAX 4
#define GEN_LANG_VI_N_NO_GLIDE 3
#define GEN_LANG_VI_N_GI_SHARES_BEFORE 1
#define GEN_LANG_VI_N_GI_NEVER_BEFORE 2
#define GEN_LANG_VI_N_RHYMES 159
#define GEN_LANG_VI_N_Q_RHYMES 38
#define GEN_LANG_VI_N_CHECKED_CODAS 4
#define GEN_LANG_VI_N_CHECKED_TONES 2
#define GEN_LANG_VI_RULE_MAX 4
#define GEN_LANG_VI_NUMBER_MAX_DIGITS 12
#define GEN_LANG_VI_N_ORDINALS 2
#define GEN_LANG_VI_N_SYMBOLS 3
#define GEN_LANG_VI_N_DICTIONARY 30
#define GEN_LANG_VI_UNIT_NONE 0xFF
#define GEN_LANG_VI_RHYME_ZERO_ONSET_ONLY 1u
#define GEN_LANG_VI_RHYME_ONSET_ONLY 2u
#define GEN_LANG_VI_NUMBER_TEN "m\306\260\341\273\235i"
#define GEN_LANG_VI_NUMBER_TENS "m\306\260\306\241i"
#define GEN_LANG_VI_NUMBER_HUNDRED "tr\304\203m"
#define GEN_LANG_VI_NUMBER_MILLION "tri\341\273\207u"
#define GEN_LANG_VI_NUMBER_BILLION "t\341\273\267"
#define GEN_LANG_VI_NUMBER_ONE_AFTER_TENS "m\341\273\221t"
#define GEN_LANG_VI_NUMBER_FOUR_AFTER_TENS "t\306\260"
#define GEN_LANG_VI_NUMBER_FIVE_AFTER_TEN "l\304\203m"
#define GEN_LANG_VI_NUMBER_DECIMAL_COMMA "ph\341\272\251y"
#define GEN_LANG_VI_NUMBER_DOT "ch\341\272\245m"
#define GEN_LANG_VI_NUMBER_ORDINAL_WORD "th\341\273\251"

typedef struct { uint32_t codepoint; uint32_t base; uint8_t tone; } gen_lang_vi_letter_t;
typedef struct { uint32_t mark; uint8_t tone; } gen_lang_vi_tone_mark_t;
typedef struct { uint32_t mark; uint32_t from; uint32_t to; } gen_lang_vi_modifier_t;
typedef struct { const char *spelling; uint8_t units[GEN_LANG_VI_N_DIALECTS]; } gen_lang_vi_onset_t;
typedef struct {
    const char *onset;
    bool only;
    uint8_t n_letters;
    uint32_t letters[GEN_LANG_VI_FRONT_MAX];
} gen_lang_vi_spelling_rule_t;
typedef struct {
    const char *spelling;
    uint8_t glide;
    uint8_t nucleus;
    uint8_t coda;
    uint8_t flags;
} gen_lang_vi_rhyme_t;
typedef struct { uint8_t from; uint8_t to; } gen_lang_vi_pair_t;
typedef struct {
    uint8_t n_glide_drops_onset;
    uint8_t glide_drops_onset[GEN_LANG_VI_RULE_MAX];
    uint8_t n_velar_codas;
    gen_lang_vi_pair_t velar_codas[GEN_LANG_VI_RULE_MAX];
    uint8_t n_velar_keep_after;
    uint8_t velar_keep_after[GEN_LANG_VI_RULE_MAX];
    uint8_t n_palatal_codas;
    gen_lang_vi_pair_t palatal_codas[GEN_LANG_VI_RULE_MAX];
    uint8_t n_centralize;
    gen_lang_vi_pair_t centralize[GEN_LANG_VI_RULE_MAX];
    uint8_t n_centralize_before;
    uint8_t centralize_before[GEN_LANG_VI_RULE_MAX];
    uint8_t n_tone;
    gen_lang_vi_pair_t tone[GEN_LANG_VI_RULE_MAX];
} gen_lang_vi_dialect_rules_t;
typedef struct { uint32_t value; const char *word; } gen_lang_vi_ordinal_t;
typedef struct { uint32_t codepoint; const char *text; } gen_lang_vi_symbol_t;
typedef struct { const char *key; const char *text; } gen_lang_vi_entry_t;

static const char *const GEN_LANG_VI_UNIT_NAMES[GEN_LANG_VI_N_UNITS] = {
    "b_<",
    "m",
    "f",
    "v",
    "t",
    "t_h",
    "d_<",
    "n",
    "s",
    "s`",
    "z",
    "j",
    "z`",
    "l",
    "c",
    "t`",
    "J",
    "k",
    "x",
    "N",
    "G",
    "h",
    "p",
    "w",
    "a:",
    "a",
    "@",
    "E",
    "e",
    "i",
    "O",
    "o",
    "@:",
    "u",
    "M",
    "i@",
    "u@",
    "M@",
    "T1",
    "T2",
    "T3",
    "T4",
    "T5",
    "T6",
};

static const uint8_t GEN_LANG_VI_TONE_UNITS[GEN_LANG_VI_N_TONES] = {
    38,
    39,
    40,
    41,
    42,
    43,
};

static const uint32_t GEN_LANG_VI_VOWEL_FORMS[GEN_LANG_VI_N_VOWELS][GEN_LANG_VI_N_TONES] = {
    {0x0061, 0x00E0, 0x00E3, 0x1EA3, 0x00E1, 0x1EA1},
    {0x0103, 0x1EB1, 0x1EB5, 0x1EB3, 0x1EAF, 0x1EB7},
    {0x00E2, 0x1EA7, 0x1EAB, 0x1EA9, 0x1EA5, 0x1EAD},
    {0x0065, 0x00E8, 0x1EBD, 0x1EBB, 0x00E9, 0x1EB9},
    {0x00EA, 0x1EC1, 0x1EC5, 0x1EC3, 0x1EBF, 0x1EC7},
    {0x0069, 0x00EC, 0x0129, 0x1EC9, 0x00ED, 0x1ECB},
    {0x006F, 0x00F2, 0x00F5, 0x1ECF, 0x00F3, 0x1ECD},
    {0x00F4, 0x1ED3, 0x1ED7, 0x1ED5, 0x1ED1, 0x1ED9},
    {0x01A1, 0x1EDD, 0x1EE1, 0x1EDF, 0x1EDB, 0x1EE3},
    {0x0075, 0x00F9, 0x0169, 0x1EE7, 0x00FA, 0x1EE5},
    {0x01B0, 0x1EEB, 0x1EEF, 0x1EED, 0x1EE9, 0x1EF1},
    {0x0079, 0x1EF3, 0x1EF9, 0x1EF7, 0x00FD, 0x1EF5},
};

static const gen_lang_vi_letter_t GEN_LANG_VI_LETTERS[GEN_LANG_VI_N_LETTERS] = {
    {0x0061, 0x0061, 0},
    {0x0041, 0x0061, 0},
    {0x00E0, 0x0061, 1},
    {0x00C0, 0x0061, 1},
    {0x00E3, 0x0061, 2},
    {0x00C3, 0x0061, 2},
    {0x1EA3, 0x0061, 3},
    {0x1EA2, 0x0061, 3},
    {0x00E1, 0x0061, 4},
    {0x00C1, 0x0061, 4},
    {0x1EA1, 0x0061, 5},
    {0x1EA0, 0x0061, 5},
    {0x0103, 0x0103, 0},
    {0x0102, 0x0103, 0},
    {0x1EB1, 0x0103, 1},
    {0x1EB0, 0x0103, 1},
    {0x1EB5, 0x0103, 2},
    {0x1EB4, 0x0103, 2},
    {0x1EB3, 0x0103, 3},
    {0x1EB2, 0x0103, 3},
    {0x1EAF, 0x0103, 4},
    {0x1EAE, 0x0103, 4},
    {0x1EB7, 0x0103, 5},
    {0x1EB6, 0x0103, 5},
    {0x00E2, 0x00E2, 0},
    {0x00C2, 0x00E2, 0},
    {0x1EA7, 0x00E2, 1},
    {0x1EA6, 0x00E2, 1},
    {0x1EAB, 0x00E2, 2},
    {0x1EAA, 0x00E2, 2},
    {0x1EA9, 0x00E2, 3},
    {0x1EA8, 0x00E2, 3},
    {0x1EA5, 0x00E2, 4},
    {0x1EA4, 0x00E2, 4},
    {0x1EAD, 0x00E2, 5},
    {0x1EAC, 0x00E2, 5},
    {0x0065, 0x0065, 0},
    {0x0045, 0x0065, 0},
    {0x00E8, 0x0065, 1},
    {0x00C8, 0x0065, 1},
    {0x1EBD, 0x0065, 2},
    {0x1EBC, 0x0065, 2},
    {0x1EBB, 0x0065, 3},
    {0x1EBA, 0x0065, 3},
    {0x00E9, 0x0065, 4},
    {0x00C9, 0x0065, 4},
    {0x1EB9, 0x0065, 5},
    {0x1EB8, 0x0065, 5},
    {0x00EA, 0x00EA, 0},
    {0x00CA, 0x00EA, 0},
    {0x1EC1, 0x00EA, 1},
    {0x1EC0, 0x00EA, 1},
    {0x1EC5, 0x00EA, 2},
    {0x1EC4, 0x00EA, 2},
    {0x1EC3, 0x00EA, 3},
    {0x1EC2, 0x00EA, 3},
    {0x1EBF, 0x00EA, 4},
    {0x1EBE, 0x00EA, 4},
    {0x1EC7, 0x00EA, 5},
    {0x1EC6, 0x00EA, 5},
    {0x0069, 0x0069, 0},
    {0x0049, 0x0069, 0},
    {0x00EC, 0x0069, 1},
    {0x00CC, 0x0069, 1},
    {0x0129, 0x0069, 2},
    {0x0128, 0x0069, 2},
    {0x1EC9, 0x0069, 3},
    {0x1EC8, 0x0069, 3},
    {0x00ED, 0x0069, 4},
    {0x00CD, 0x0069, 4},
    {0x1ECB, 0x0069, 5},
    {0x1ECA, 0x0069, 5},
    {0x006F, 0x006F, 0},
    {0x004F, 0x006F, 0},
    {0x00F2, 0x006F, 1},
    {0x00D2, 0x006F, 1},
    {0x00F5, 0x006F, 2},
    {0x00D5, 0x006F, 2},
    {0x1ECF, 0x006F, 3},
    {0x1ECE, 0x006F, 3},
    {0x00F3, 0x006F, 4},
    {0x00D3, 0x006F, 4},
    {0x1ECD, 0x006F, 5},
    {0x1ECC, 0x006F, 5},
    {0x00F4, 0x00F4, 0},
    {0x00D4, 0x00F4, 0},
    {0x1ED3, 0x00F4, 1},
    {0x1ED2, 0x00F4, 1},
    {0x1ED7, 0x00F4, 2},
    {0x1ED6, 0x00F4, 2},
    {0x1ED5, 0x00F4, 3},
    {0x1ED4, 0x00F4, 3},
    {0x1ED1, 0x00F4, 4},
    {0x1ED0, 0x00F4, 4},
    {0x1ED9, 0x00F4, 5},
    {0x1ED8, 0x00F4, 5},
    {0x01A1, 0x01A1, 0},
    {0x01A0, 0x01A1, 0},
    {0x1EDD, 0x01A1, 1},
    {0x1EDC, 0x01A1, 1},
    {0x1EE1, 0x01A1, 2},
    {0x1EE0, 0x01A1, 2},
    {0x1EDF, 0x01A1, 3},
    {0x1EDE, 0x01A1, 3},
    {0x1EDB, 0x01A1, 4},
    {0x1EDA, 0x01A1, 4},
    {0x1EE3, 0x01A1, 5},
    {0x1EE2, 0x01A1, 5},
    {0x0075, 0x0075, 0},
    {0x0055, 0x0075, 0},
    {0x00F9, 0x0075, 1},
    {0x00D9, 0x0075, 1},
    {0x0169, 0x0075, 2},
    {0x0168, 0x0075, 2},
    {0x1EE7, 0x0075, 3},
    {0x1EE6, 0x0075, 3},
    {0x00FA, 0x0075, 4},
    {0x00DA, 0x0075, 4},
    {0x1EE5, 0x0075, 5},
    {0x1EE4, 0x0075, 5},
    {0x01B0, 0x01B0, 0},
    {0x01AF, 0x01B0, 0},
    {0x1EEB, 0x01B0, 1},
    {0x1EEA, 0x01B0, 1},
    {0x1EEF, 0x01B0, 2},
    {0x1EEE, 0x01B0, 2},
    {0x1EED, 0x01B0, 3},
    {0x1EEC, 0x01B0, 3},
    {0x1EE9, 0x01B0, 4},
    {0x1EE8, 0x01B0, 4},
    {0x1EF1, 0x01B0, 5},
    {0x1EF0, 0x01B0, 5},
    {0x0079, 0x0079, 0},
    {0x0059, 0x0079, 0},
    {0x1EF3, 0x0079, 1},
    {0x1EF2, 0x0079, 1},
    {0x1EF9, 0x0079, 2},
    {0x1EF8, 0x0079, 2},
    {0x1EF7, 0x0079, 3},
    {0x1EF6, 0x0079, 3},
    {0x00FD, 0x0079, 4},
    {0x00DD, 0x0079, 4},
    {0x1EF5, 0x0079, 5},
    {0x1EF4, 0x0079, 5},
    {0x0111, 0x0111, 0},
    {0x0110, 0x0111, 0},
};

static const gen_lang_vi_tone_mark_t GEN_LANG_VI_TONE_MARKS[GEN_LANG_VI_N_TONE_MARKS] = {
    {0x0300, 1},
    {0x0303, 2},
    {0x0309, 3},
    {0x0301, 4},
    {0x0323, 5},
    {0x0340, 1},
    {0x0341, 4},
};

static const gen_lang_vi_modifier_t GEN_LANG_VI_MODIFIERS[GEN_LANG_VI_N_MODIFIERS] = {
    {0x0302, 0x0061, 0x00E2},
    {0x0302, 0x0065, 0x00EA},
    {0x0302, 0x006F, 0x00F4},
    {0x0306, 0x0061, 0x0103},
    {0x031B, 0x006F, 0x01A1},
    {0x031B, 0x0075, 0x01B0},
};

static const gen_lang_vi_onset_t GEN_LANG_VI_ONSETS[GEN_LANG_VI_N_ONSETS] = {
    {"ngh", {19, 19, 19}},
    {"ch", {14, 14, 14}},
    {"\304\221", {6, 6, 6}},
    {"gh", {20, 20, 20}},
    {"gi", {10, 11, 11}},
    {"kh", {18, 18, 18}},
    {"ng", {19, 19, 19}},
    {"nh", {16, 16, 16}},
    {"ph", {2, 2, 2}},
    {"qu", {17, 17, 17}},
    {"th", {5, 5, 5}},
    {"tr", {14, 15, 15}},
    {"b", {0, 0, 0}},
    {"c", {17, 17, 17}},
    {"d", {10, 11, 11}},
    {"g", {20, 20, 20}},
    {"h", {21, 21, 21}},
    {"k", {17, 17, 17}},
    {"l", {13, 13, 13}},
    {"m", {1, 1, 1}},
    {"n", {7, 7, 7}},
    {"p", {22, 22, 22}},
    {"r", {10, 12, 12}},
    {"s", {8, 9, 9}},
    {"t", {4, 4, 4}},
    {"v", {3, 3, 11}},
    {"x", {8, 8, 8}},
};

static const gen_lang_vi_spelling_rule_t GEN_LANG_VI_SPELLING_RULES[GEN_LANG_VI_N_SPELLING_RULES] = {
    {"k", true, 4, {0x0069, 0x0065, 0x00EA, 0x0079}},
    {"gh", true, 3, {0x0069, 0x0065, 0x00EA}},
    {"ngh", true, 3, {0x0069, 0x0065, 0x00EA}},
    {"c", false, 4, {0x0069, 0x0065, 0x00EA, 0x0079}},
    {"g", false, 4, {0x0069, 0x0065, 0x00EA, 0x0079}},
    {"ng", false, 4, {0x0069, 0x0065, 0x00EA, 0x0079}},
};

static const char *const GEN_LANG_VI_NO_GLIDE[GEN_LANG_VI_N_NO_GLIDE] = {
    "c",
    "k",
    "gi",
};

static const uint32_t GEN_LANG_VI_GI_SHARES_BEFORE[GEN_LANG_VI_N_GI_SHARES_BEFORE] = {
    0x00EA,
};

static const uint32_t GEN_LANG_VI_GI_NEVER_BEFORE[GEN_LANG_VI_N_GI_NEVER_BEFORE] = {
    0x0069,
    0x0079,
};

static const gen_lang_vi_rhyme_t GEN_LANG_VI_RHYMES[GEN_LANG_VI_N_RHYMES] = {
    {"a", GEN_LANG_VI_UNIT_NONE, 24, GEN_LANG_VI_UNIT_NONE, 0},
    {"ai", GEN_LANG_VI_UNIT_NONE, 24, 11, 0},
    {"ao", GEN_LANG_VI_UNIT_NONE, 24, 23, 0},
    {"am", GEN_LANG_VI_UNIT_NONE, 24, 1, 0},
    {"an", GEN_LANG_VI_UNIT_NONE, 24, 7, 0},
    {"ang", GEN_LANG_VI_UNIT_NONE, 24, 19, 0},
    {"anh", GEN_LANG_VI_UNIT_NONE, 25, 16, 0},
    {"ap", GEN_LANG_VI_UNIT_NONE, 24, 22, 0},
    {"at", GEN_LANG_VI_UNIT_NONE, 24, 4, 0},
    {"ac", GEN_LANG_VI_UNIT_NONE, 24, 17, 0},
    {"ach", GEN_LANG_VI_UNIT_NONE, 25, 14, 0},
    {"ay", GEN_LANG_VI_UNIT_NONE, 25, 11, 0},
    {"au", GEN_LANG_VI_UNIT_NONE, 25, 23, 0},
    {"\304\203m", GEN_LANG_VI_UNIT_NONE, 25, 1, 0},
    {"\304\203n", GEN_LANG_VI_UNIT_NONE, 25, 7, 0},
    {"\304\203ng", GEN_LANG_VI_UNIT_NONE, 25, 19, 0},
    {"\304\203p", GEN_LANG_VI_UNIT_NONE, 25, 22, 0},
    {"\304\203t", GEN_LANG_VI_UNIT_NONE, 25, 4, 0},
    {"\304\203c", GEN_LANG_VI_UNIT_NONE, 25, 17, 0},
    {"\303\242m", GEN_LANG_VI_UNIT_NONE, 26, 1, 0},
    {"\303\242n", GEN_LANG_VI_UNIT_NONE, 26, 7, 0},
    {"\303\242ng", GEN_LANG_VI_UNIT_NONE, 26, 19, 0},
    {"\303\242p", GEN_LANG_VI_UNIT_NONE, 26, 22, 0},
    {"\303\242t", GEN_LANG_VI_UNIT_NONE, 26, 4, 0},
    {"\303\242c", GEN_LANG_VI_UNIT_NONE, 26, 17, 0},
    {"\303\242y", GEN_LANG_VI_UNIT_NONE, 26, 11, 0},
    {"\303\242u", GEN_LANG_VI_UNIT_NONE, 26, 23, 0},
    {"e", GEN_LANG_VI_UNIT_NONE, 27, GEN_LANG_VI_UNIT_NONE, 0},
    {"eo", GEN_LANG_VI_UNIT_NONE, 27, 23, 0},
    {"em", GEN_LANG_VI_UNIT_NONE, 27, 1, 0},
    {"en", GEN_LANG_VI_UNIT_NONE, 27, 7, 0},
    {"eng", GEN_LANG_VI_UNIT_NONE, 27, 19, 0},
    {"ep", GEN_LANG_VI_UNIT_NONE, 27, 22, 0},
    {"et", GEN_LANG_VI_UNIT_NONE, 27, 4, 0},
    {"ec", GEN_LANG_VI_UNIT_NONE, 27, 17, 0},
    {"\303\252", GEN_LANG_VI_UNIT_NONE, 28, GEN_LANG_VI_UNIT_NONE, 0},
    {"\303\252u", GEN_LANG_VI_UNIT_NONE, 28, 23, 0},
    {"\303\252m", GEN_LANG_VI_UNIT_NONE, 28, 1, 0},
    {"\303\252n", GEN_LANG_VI_UNIT_NONE, 28, 7, 0},
    {"\303\252nh", GEN_LANG_VI_UNIT_NONE, 28, 16, 0},
    {"\303\252p", GEN_LANG_VI_UNIT_NONE, 28, 22, 0},
    {"\303\252t", GEN_LANG_VI_UNIT_NONE, 28, 4, 0},
    {"\303\252ch", GEN_LANG_VI_UNIT_NONE, 28, 14, 0},
    {"i", GEN_LANG_VI_UNIT_NONE, 29, GEN_LANG_VI_UNIT_NONE, 0},
    {"y", GEN_LANG_VI_UNIT_NONE, 29, GEN_LANG_VI_UNIT_NONE, 0},
    {"iu", GEN_LANG_VI_UNIT_NONE, 29, 23, 0},
    {"im", GEN_LANG_VI_UNIT_NONE, 29, 1, 0},
    {"in", GEN_LANG_VI_UNIT_NONE, 29, 7, 0},
    {"inh", GEN_LANG_VI_UNIT_NONE, 29, 16, 0},
    {"ip", GEN_LANG_VI_UNIT_NONE, 29, 22, 0},
    {"it", GEN_LANG_VI_UNIT_NONE, 29, 4, 0},
    {"ich", GEN_LANG_VI_UNIT_NONE, 29, 14, 0},
    {"ia", GEN_LANG_VI_UNIT_NONE, 35, GEN_LANG_VI_UNIT_NONE, 0},
    {"i\303\252u", GEN_LANG_VI_UNIT_NONE, 35, 23, 2},
    {"i\303\252m", GEN_LANG_VI_UNIT_NONE, 35, 1, 2},
    {"i\303\252n", GEN_LANG_VI_UNIT_NONE, 35, 7, 2},
    {"i\303\252ng", GEN_LANG_VI_UNIT_NONE, 35, 19, 2},
    {"i\303\252p", GEN_LANG_VI_UNIT_NONE, 35, 22, 2},
    {"i\303\252t", GEN_LANG_VI_UNIT_NONE, 35, 4, 2},
    {"i\303\252c", GEN_LANG_VI_UNIT_NONE, 35, 17, 2},
    {"y\303\252u", GEN_LANG_VI_UNIT_NONE, 35, 23, 1},
    {"y\303\252m", GEN_LANG_VI_UNIT_NONE, 35, 1, 1},
    {"y\303\252n", GEN_LANG_VI_UNIT_NONE, 35, 7, 1},
    {"y\303\252ng", GEN_LANG_VI_UNIT_NONE, 35, 19, 1},
    {"y\303\252t", GEN_LANG_VI_UNIT_NONE, 35, 4, 1},
    {"o", GEN_LANG_VI_UNIT_NONE, 30, GEN_LANG_VI_UNIT_NONE, 0},
    {"oi", GEN_LANG_VI_UNIT_NONE, 30, 11, 0},
    {"om", GEN_LANG_VI_UNIT_NONE, 30, 1, 0},
    {"on", GEN_LANG_VI_UNIT_NONE, 30, 7, 0},
    {"ong", GEN_LANG_VI_UNIT_NONE, 30, 19, 0},
    {"op", GEN_LANG_VI_UNIT_NONE, 30, 22, 0},
    {"ot", GEN_LANG_VI_UNIT_NONE, 30, 4, 0},
    {"oc", GEN_LANG_VI_UNIT_NONE, 30, 17, 0},
    {"oong", GEN_LANG_VI_UNIT_NONE, 30, 19, 0},
    {"ooc", GEN_LANG_VI_UNIT_NONE, 30, 17, 0},
    {"\303\264", GEN_LANG_VI_UNIT_NONE, 31, GEN_LANG_VI_UNIT_NONE, 0},
    {"\303\264i", GEN_LANG_VI_UNIT_NONE, 31, 11, 0},
    {"\303\264m", GEN_LANG_VI_UNIT_NONE, 31, 1, 0},
    {"\303\264n", GEN_LANG_VI_UNIT_NONE, 31, 7, 0},
    {"\303\264ng", GEN_LANG_VI_UNIT_NONE, 31, 19, 0},
    {"\303\264p", GEN_LANG_VI_UNIT_NONE, 31, 22, 0},
    {"\303\264t", GEN_LANG_VI_UNIT_NONE, 31, 4, 0},
    {"\303\264c", GEN_LANG_VI_UNIT_NONE, 31, 17, 0},
    {"\306\241", GEN_LANG_VI_UNIT_NONE, 32, GEN_LANG_VI_UNIT_NONE, 0},
    {"\306\241i", GEN_LANG_VI_UNIT_NONE, 32, 11, 0},
    {"\306\241m", GEN_LANG_VI_UNIT_NONE, 32, 1, 0},
    {"\306\241n", GEN_LANG_VI_UNIT_NONE, 32, 7, 0},
    {"\306\241p", GEN_LANG_VI_UNIT_NONE, 32, 22, 0},
    {"\306\241t", GEN_LANG_VI_UNIT_NONE, 32, 4, 0},
    {"u", GEN_LANG_VI_UNIT_NONE, 33, GEN_LANG_VI_UNIT_NONE, 0},
    {"ui", GEN_LANG_VI_UNIT_NONE, 33, 11, 0},
    {"um", GEN_LANG_VI_UNIT_NONE, 33, 1, 0},
    {"un", GEN_LANG_VI_UNIT_NONE, 33, 7, 0},
    {"ung", GEN_LANG_VI_UNIT_NONE, 33, 19, 0},
    {"up", GEN_LANG_VI_UNIT_NONE, 33, 22, 0},
    {"ut", GEN_LANG_VI_UNIT_NONE, 33, 4, 0},
    {"uc", GEN_LANG_VI_UNIT_NONE, 33, 17, 0},
    {"ua", GEN_LANG_VI_UNIT_NONE, 36, GEN_LANG_VI_UNIT_NONE, 0},
    {"u\303\264i", GEN_LANG_VI_UNIT_NONE, 36, 11, 0},
    {"u\303\264m", GEN_LANG_VI_UNIT_NONE, 36, 1, 0},
    {"u\303\264n", GEN_LANG_VI_UNIT_NONE, 36, 7, 0},
    {"u\303\264ng", GEN_LANG_VI_UNIT_NONE, 36, 19, 0},
    {"u\303\264t", GEN_LANG_VI_UNIT_NONE, 36, 4, 0},
    {"u\303\264c", GEN_LANG_VI_UNIT_NONE, 36, 17, 0},
    {"\306\260", GEN_LANG_VI_UNIT_NONE, 34, GEN_LANG_VI_UNIT_NONE, 0},
    {"\306\260i", GEN_LANG_VI_UNIT_NONE, 34, 11, 0},
    {"\306\260u", GEN_LANG_VI_UNIT_NONE, 34, 23, 0},
    {"\306\260m", GEN_LANG_VI_UNIT_NONE, 34, 1, 0},
    {"\306\260n", GEN_LANG_VI_UNIT_NONE, 34, 7, 0},
    {"\306\260ng", GEN_LANG_VI_UNIT_NONE, 34, 19, 0},
    {"\306\260t", GEN_LANG_VI_UNIT_NONE, 34, 4, 0},
    {"\306\260c", GEN_LANG_VI_UNIT_NONE, 34, 17, 0},
    {"\306\260a", GEN_LANG_VI_UNIT_NONE, 37, GEN_LANG_VI_UNIT_NONE, 0},
    {"\306\260\306\241i", GEN_LANG_VI_UNIT_NONE, 37, 11, 0},
    {"\306\260\306\241u", GEN_LANG_VI_UNIT_NONE, 37, 23, 0},
    {"\306\260\306\241m", GEN_LANG_VI_UNIT_NONE, 37, 1, 0},
    {"\306\260\306\241n", GEN_LANG_VI_UNIT_NONE, 37, 7, 0},
    {"\306\260\306\241ng", GEN_LANG_VI_UNIT_NONE, 37, 19, 0},
    {"\306\260\306\241p", GEN_LANG_VI_UNIT_NONE, 37, 22, 0},
    {"\306\260\306\241t", GEN_LANG_VI_UNIT_NONE, 37, 4, 0},
    {"\306\260\306\241c", GEN_LANG_VI_UNIT_NONE, 37, 17, 0},
    {"oa", 23, 24, GEN_LANG_VI_UNIT_NONE, 0},
    {"oai", 23, 24, 11, 0},
    {"oao", 23, 24, 23, 0},
    {"oam", 23, 24, 1, 0},
    {"oan", 23, 24, 7, 0},
    {"oang", 23, 24, 19, 0},
    {"oanh", 23, 25, 16, 0},
    {"oap", 23, 24, 22, 0},
    {"oat", 23, 24, 4, 0},
    {"oac", 23, 24, 17, 0},
    {"oach", 23, 25, 14, 0},
    {"oay", 23, 25, 11, 0},
    {"o\304\203m", 23, 25, 1, 0},
    {"o\304\203n", 23, 25, 7, 0},
    {"o\304\203ng", 23, 25, 19, 0},
    {"o\304\203t", 23, 25, 4, 0},
    {"o\304\203c", 23, 25, 17, 0},
    {"oe", 23, 27, GEN_LANG_VI_UNIT_NONE, 0},
    {"oeo", 23, 27, 23, 0},
    {"oen", 23, 27, 7, 0},
    {"oet", 23, 27, 4, 0},
    {"u\303\242n", 23, 26, 7, 0},
    {"u\303\242ng", 23, 26, 19, 0},
    {"u\303\242t", 23, 26, 4, 0},
    {"u\303\242y", 23, 26, 11, 0},
    {"u\303\252", 23, 28, GEN_LANG_VI_UNIT_NONE, 0},
    {"u\303\252nh", 23, 28, 16, 0},
    {"u\303\252ch", 23, 28, 14, 0},
    {"u\306\241", 23, 32, GEN_LANG_VI_UNIT_NONE, 0},
    {"uy", 23, 29, GEN_LANG_VI_UNIT_NONE, 0},
    {"uya", 23, 35, GEN_LANG_VI_UNIT_NONE, 0},
    {"uyu", 23, 29, 23, 0},
    {"uynh", 23, 29, 16, 0},
    {"uych", 23, 29, 14, 0},
    {"uyt", 23, 29, 4, 0},
    {"uyp", 23, 29, 22, 0},
    {"uy\303\252n", 23, 35, 7, 0},
    {"uy\303\252t", 23, 35, 4, 0},
};

static const gen_lang_vi_rhyme_t GEN_LANG_VI_Q_RHYMES[GEN_LANG_VI_N_Q_RHYMES] = {
    {"a", 23, 24, GEN_LANG_VI_UNIT_NONE, 0},
    {"ai", 23, 24, 11, 0},
    {"ao", 23, 24, 23, 0},
    {"an", 23, 24, 7, 0},
    {"ang", 23, 24, 19, 0},
    {"anh", 23, 25, 16, 0},
    {"at", 23, 24, 4, 0},
    {"ac", 23, 24, 17, 0},
    {"ach", 23, 25, 14, 0},
    {"ay", 23, 25, 11, 0},
    {"\304\203m", 23, 25, 1, 0},
    {"\304\203n", 23, 25, 7, 0},
    {"\304\203ng", 23, 25, 19, 0},
    {"\304\203p", 23, 25, 22, 0},
    {"\304\203t", 23, 25, 4, 0},
    {"\304\203c", 23, 25, 17, 0},
    {"\303\242n", 23, 26, 7, 0},
    {"\303\242ng", 23, 26, 19, 0},
    {"\303\242t", 23, 26, 4, 0},
    {"\303\242y", 23, 26, 11, 0},
    {"e", 23, 27, GEN_LANG_VI_UNIT_NONE, 0},
    {"eo", 23, 27, 23, 0},
    {"en", 23, 27, 7, 0},
    {"et", 23, 27, 4, 0},
    {"\303\252", 23, 28, GEN_LANG_VI_UNIT_NONE, 0},
    {"\303\252n", 23, 28, 7, 0},
    {"\303\252t", 23, 28, 4, 0},
    {"\303\252nh", 23, 28, 16, 0},
    {"\303\252ch", 23, 28, 14, 0},
    {"i", 23, 29, GEN_LANG_VI_UNIT_NONE, 0},
    {"y", 23, 29, GEN_LANG_VI_UNIT_NONE, 0},
    {"ynh", 23, 29, 16, 0},
    {"ych", 23, 29, 14, 0},
    {"yt", 23, 29, 4, 0},
    {"y\303\252n", 23, 35, 7, 0},
    {"y\303\252t", 23, 35, 4, 0},
    {"\306\241", 23, 32, GEN_LANG_VI_UNIT_NONE, 0},
    {"\303\264c", GEN_LANG_VI_UNIT_NONE, 36, 17, 0},
};

static const uint8_t GEN_LANG_VI_CHECKED_CODAS[GEN_LANG_VI_N_CHECKED_CODAS] = {
    22,
    4,
    17,
    14,
};

static const uint8_t GEN_LANG_VI_CHECKED_TONES[GEN_LANG_VI_N_CHECKED_TONES] = {
    42,
    43,
};

static const gen_lang_vi_dialect_rules_t GEN_LANG_VI_DIALECT_RULES[GEN_LANG_VI_N_DIALECTS] = {
    {.n_glide_drops_onset = 0, .glide_drops_onset = {0}, .n_velar_codas = 0, .velar_codas = {{0, 0}}, .n_velar_keep_after = 0, .velar_keep_after = {0}, .n_palatal_codas = 0, .palatal_codas = {{0, 0}}, .n_centralize = 0, .centralize = {{0, 0}}, .n_centralize_before = 0, .centralize_before = {0}, .n_tone = 0, .tone = {{0, 0}}},
    {.n_glide_drops_onset = 0, .glide_drops_onset = {0}, .n_velar_codas = 2, .velar_codas = {{7, 19}, {4, 17}}, .n_velar_keep_after = 2, .velar_keep_after = {29, 28}, .n_palatal_codas = 0, .palatal_codas = {{0, 0}}, .n_centralize = 0, .centralize = {{0, 0}}, .n_centralize_before = 0, .centralize_before = {0}, .n_tone = 1, .tone = {{40, 41}}},
    {.n_glide_drops_onset = 2, .glide_drops_onset = {17, 21}, .n_velar_codas = 2, .velar_codas = {{7, 19}, {4, 17}}, .n_velar_keep_after = 2, .velar_keep_after = {29, 28}, .n_palatal_codas = 2, .palatal_codas = {{16, 7}, {14, 4}}, .n_centralize = 2, .centralize = {{29, 34}, {28, 26}}, .n_centralize_before = 4, .centralize_before = {7, 4, 16, 14}, .n_tone = 1, .tone = {{40, 41}}},
};

static const char *const GEN_LANG_VI_NUMBER_DIGITS[10] = {
    "kh\303\264ng",
    "m\341\273\231t",
    "hai",
    "ba",
    "b\341\273\221n",
    "n\304\203m",
    "s\303\241u",
    "b\341\272\243y",
    "t\303\241m",
    "ch\303\255n",
};

static const char *const GEN_LANG_VI_NUMBER_THOUSAND[GEN_LANG_VI_N_DIALECTS] = {
    "ngh\303\254n",
    "ng\303\240n",
    "ng\303\240n",
};

static const char *const GEN_LANG_VI_NUMBER_ZERO_TENS[GEN_LANG_VI_N_DIALECTS] = {
    "linh",
    "l\341\272\273",
    "l\341\272\273",
};

static const gen_lang_vi_ordinal_t GEN_LANG_VI_ORDINALS[GEN_LANG_VI_N_ORDINALS] = {
    {1, "nh\341\272\245t"},
    {4, "t\306\260"},
};

static const gen_lang_vi_symbol_t GEN_LANG_VI_SYMBOLS[GEN_LANG_VI_N_SYMBOLS] = {
    {0x0025, "ph\341\272\247n tr\304\203m"},
    {0x0026, "v\303\240"},
    {0x002B, "c\341\273\231ng"},
};

static const gen_lang_vi_entry_t GEN_LANG_VI_DICTIONARY[GEN_LANG_VI_N_DICTIONARY] = {
    {"app", "\303\241p"},
    {"bluetooth", "b\341\273\235 lu t\303\272t"},
    {"camera", "ca m\303\252 ra"},
    {"cm", "xen ti m\303\251t"},
    {"email", "i meo"},
    {"facebook", "ph\303\242y b\303\272c"},
    {"fm", "\303\251p em"},
    {"google", "gu g\341\273\223"},
    {"h", "gi\341\273\235"},
    {"hcm", "h\341\273\223 ch\303\255 minh"},
    {"internet", "in t\306\241 n\303\251t"},
    {"karaoke", "ca ra \303\264 k\303\252"},
    {"kg", "ki l\303\264 gam"},
    {"km", "ki l\303\264 m\303\251t"},
    {"laptop", "l\303\241p t\341\273\221p"},
    {"led", "l\303\251t"},
    {"ok", "\303\264 k\303\252"},
    {"okay", "\303\264 k\303\252"},
    {"radio", "ra \304\221i \303\264"},
    {"tivi", "ti vi"},
    {"tp", "th\303\240nh ph\341\273\221"},
    {"tv", "ti vi"},
    {"usb", "u \303\251t b\303\252"},
    {"video", "vi \304\221\303\252 \303\264"},
    {"vn", "vi\341\273\207t nam"},
    {"wi-fi", "oai phai"},
    {"wifi", "oai phai"},
    {"youtube", "diu t\303\272p"},
    {"zalo", "da l\303\264"},
    {"\304\221", "\304\221\341\273\223ng"},
};
