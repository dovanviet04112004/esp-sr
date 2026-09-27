#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "lang_vi.h"
#include "parity.h"
#include "test_report.h"

#define CASE_BYTES_MAX (512 * 1024)
#define REPORT_LINES_MAX 256

static unsigned s_failures;

static void check(bool ok, const char *what)
{
    printf("HOST %s: %s\n", ok ? "PASS" : "FAIL", what);
    s_failures += ok ? 0u : 1u;
}

static bool units_are(const lang_vi_unit_t *units, size_t n, const char *const *names, size_t n_names)
{
    bool same = n == n_names;
    for (size_t i = 0; same && i < n; i++) {
        same = strcmp(lang_vi_unit_name(units[i]), names[i]) == 0;
    }
    return same;
}

static void check_normalize(void)
{
    char out[LANG_VI_TEXT_MAX_BYTES];
    char tiny[4];
    check(lang_vi_normalize("B\xe1\xba\xacT \xc4\x90\xc3\x88N 105!", out, sizeof(out)) == ESP_OK &&
              strcmp(out, "b\xe1\xba\xadt \xc4\x91\xc3\xa8n m\xe1\xbb\x99t tr\xc4\x83m linh n\xc4\x83m") == 0,
          "normalize: upper case lowered, 105 read the northern way, punctuation dropped");
    check(lang_vi_normalize("b\xe1\xba\xadt", tiny, sizeof(tiny)) == ESP_ERR_INVALID_SIZE &&
              lang_vi_normalize("", tiny, 0) == ESP_ERR_INVALID_SIZE,
          "normalize: a short buffer is refused");
    const char *bad[] = {"\xc3", "\xc0\xaf", "\xed\xa0\x80", "\xf4\x90\x80\x80", "\xff"};
    bool refused = lang_vi_normalize(NULL, out, sizeof(out)) == ESP_ERR_INVALID_ARG;
    for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        refused = refused && lang_vi_normalize(bad[i], out, sizeof(out)) == ESP_ERR_INVALID_ARG;
    }
    check(refused, "normalize: truncated, overlong, surrogate, past U+10FFFF and stray bytes are not UTF-8");
}

static void check_g2p_and_lexicon(void)
{
    static const char *const kNorth[] = {"b_<", "@", "t", "T6", "d_<", "E", "n", "T2"};
    static const char *const kSouth[] = {"b_<", "@", "k", "T6", "d_<", "E", "N", "T2"};
    const char *bat_den = "b\xe1\xba\xadt \xc4\x91\xc3\xa8n";
    lang_vi_unit_t units[LANG_VI_UNITS_MAX];
    size_t n_units = 7;
    check(lang_vi_g2p(bat_den, LANG_VI_DIALECT_NORTH, units, LANG_VI_UNITS_MAX, &n_units) == ESP_OK &&
              units_are(units, n_units, kNorth, 8),
          "g2p: bat den in the north");
    check(lang_vi_g2p(bat_den, LANG_VI_DIALECT_ALL, units, LANG_VI_UNITS_MAX, &n_units) ==
                  ESP_ERR_INVALID_ARG &&
              n_units == 0 &&
              lang_vi_g2p(bat_den, LANG_VI_DIALECT_NORTH, units, 4, &n_units) == ESP_ERR_INVALID_SIZE,
          "g2p: more than one dialect is refused, and so is a reading longer than cap");
    lang_vi_pron_t pron;
    check(lang_vi_lexicon_entry(bat_den, LANG_VI_DIALECT_ALL, &pron) == ESP_OK && pron.n_variants == 2 &&
              units_are(pron.units[0], pron.n_units[0], kNorth, 8) &&
              units_are(pron.units[1], pron.n_units[1], kSouth, 8),
          "lexicon: north, then centre and south read alike");
    check(lang_vi_lexicon_entry("\xc0\xaf", LANG_VI_DIALECT_ALL, &pron) == ESP_ERR_INVALID_ARG &&
              lang_vi_lexicon_entry("bat", 0, &pron) == ESP_ERR_INVALID_ARG &&
              lang_vi_lexicon_entry("!!!", LANG_VI_DIALECT_ALL, &pron) == ESP_ERR_INVALID_ARG,
          "lexicon: bad UTF-8, an empty mask and a line without a syllable are refused");
    check(strcmp(lang_vi_unit_name(0), "b_<") == 0 && strcmp(lang_vi_unit_name(200), "?") == 0,
          "unit_name: known ids by name, unknown ones as ?");
}

static bool read_case(const char *path, void *buf, size_t cap, size_t *len)
{
    FILE *file = fopen(path, "rb");
    if (file == NULL) { return false; }
    *len = fread(buf, 1, cap, file);
    const bool whole = feof(file) != 0;
    fclose(file);
    return whole;
}

static void run_golden(const char *root)
{
    static uint8_t buf[CASE_BYTES_MAX];
    test_report_begin("PARITY", REPORT_LINES_MAX);
    const unsigned cases =
        parity_run_block(root, "g2p", parity_g2p, read_case, buf, sizeof(buf), &s_failures) +
        parity_run_block(root, "normalize", parity_normalize, read_case, buf, sizeof(buf), &s_failures) +
        parity_run_block(root, "lexicon", parity_lexicon, read_case, buf, sizeof(buf), &s_failures);
    test_report_line("done %u cases", cases);
    test_report_serve();
}

int main(int argc, char **argv)
{
    check_normalize();
    check_g2p_and_lexicon();
    if (argc > 1) { run_golden(argv[1]); }
    printf("HOST %u failure(s)\n", s_failures);
    return s_failures == 0 ? 0 : 1;
}
