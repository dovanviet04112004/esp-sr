#include <stdlib.h>
#include <string.h>

#include "lang_vi.h"
#include "parity.h"

#define N_DIALECTS 3
#define UNIT_PAD 0xFF

// The next NUL-ended string of a u8 tensor from *offset, which it advances; NULL past the end.
static const char *next_text(const gold_tensor_t *t, size_t *offset)
{
    const char *start = (const char *)t->data + *offset;
    const void *nul = memchr(start, '\0', t->nbytes > *offset ? t->nbytes - *offset : 0);
    if (nul == NULL) { return NULL; }
    *offset += (size_t)((const char *)nul - start) + 1;
    return start;
}

static bool tensors(const void *buf, size_t len, const char *const *names, gold_tensor_t *out, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        if (!parity_tensor(buf, len, names[i], &out[i])) { return false; }
    }
    return out[0].dtype == GOLD_U8;
}

// want and got hold n floats each, one allocation.
static float *pair_of(size_t n)
{
    return malloc(2 * (n > 0 ? n : 1) * sizeof(float));
}

bool parity_g2p(const char *case_name, const void *buf, size_t len)
{
    static const char *const kNames[] = {"text", "status", "units"};
    gold_tensor_t t[3];
    if (!tensors(buf, len, kNames, t, 3) || t[2].ndim != 3 || t[2].dims[1] != N_DIALECTS ||
        t[2].dims[2] > LANG_VI_UNITS_MAX || t[1].ndim != 2 || t[1].dims[0] != t[2].dims[0]) {
        return false;
    }
    const size_t n = t[2].dims[0], width = t[2].dims[2], rows = n * N_DIALECTS;
    float *status = pair_of(rows), *units = pair_of(rows * width);
    bool ok = status != NULL && units != NULL && parity_floats(&t[1], status, rows) &&
              parity_floats(&t[2], units, rows * width);
    size_t offset = 0;
    for (size_t i = 0; ok && i < n; i++) {
        const char *item = next_text(&t[0], &offset);
        ok = item != NULL;
        for (size_t d = 0; ok && d < N_DIALECTS; d++) {
            lang_vi_unit_t out[LANG_VI_UNITS_MAX];
            size_t k = 0;
            const esp_err_t err = lang_vi_g2p(item, (lang_vi_dialect_t)(1u << d), out, width, &k);
            const size_t row = i * N_DIALECTS + d;
            status[rows + row] = (float)err;
            for (size_t j = 0; j < width; j++) {
                units[rows * width + row * width + j] = err == ESP_OK && j < k ? out[j] : UNIT_PAD;
            }
        }
    }
    if (ok) {
        parity_report("g2p", case_name, "status", status, status + rows, rows);
        parity_report("g2p", case_name, "units", units, units + rows * width, rows * width);
    }
    free(units);
    free(status);
    return ok;
}

bool parity_normalize(const char *case_name, const void *buf, size_t len)
{
    static const char *const kNames[] = {"text", "status", "output"};
    gold_tensor_t t[3];
    if (!tensors(buf, len, kNames, t, 3) || t[2].ndim != 2 || t[2].dims[1] > LANG_VI_TEXT_MAX_BYTES ||
        t[1].ndim != 1 || t[1].dims[0] != t[2].dims[0]) {
        return false;
    }
    const size_t n = t[2].dims[0], cap = t[2].dims[1];
    float *status = pair_of(n), *output = pair_of(n * cap);
    bool ok = status != NULL && output != NULL && parity_floats(&t[1], status, n) &&
              parity_floats(&t[2], output, n * cap);
    size_t offset = 0;
    for (size_t i = 0; ok && i < n; i++) {
        const char *item = next_text(&t[0], &offset);
        char out[LANG_VI_TEXT_MAX_BYTES] = {0};
        ok = item != NULL;
        const esp_err_t err = ok ? lang_vi_normalize(item, out, cap) : ESP_FAIL;
        const size_t kept = err == ESP_OK ? strlen(out) : 0;
        status[n + i] = (float)err;
        for (size_t j = 0; j < cap; j++) {
            output[n * cap + i * cap + j] = j < kept ? (float)(uint8_t)out[j] : 0.0f;
        }
    }
    if (ok) {
        parity_report("normalize", case_name, "status", status, status + n, n);
        parity_report("normalize", case_name, "output", output, output + n * cap, n * cap);
    }
    free(output);
    free(status);
    return ok;
}

bool parity_lexicon(const char *case_name, const void *buf, size_t len)
{
    static const char *const kNames[] = {"text", "mask", "status", "n_variants", "n_units", "units"};
    gold_tensor_t t[6];
    if (!tensors(buf, len, kNames, t, 6) || t[5].ndim != 3 || t[5].dims[1] != LANG_VI_VARIANTS_MAX ||
        t[5].dims[2] != LANG_VI_UNITS_MAX || t[1].dtype != GOLD_U8 || t[1].dims[0] != t[5].dims[0]) {
        return false;
    }
    const size_t n = t[5].dims[0], per_line = LANG_VI_VARIANTS_MAX * LANG_VI_UNITS_MAX;
    float *status = pair_of(n), *n_variants = pair_of(n), *n_units = pair_of(n * LANG_VI_VARIANTS_MAX);
    float *units = pair_of(n * per_line);
    bool ok = status != NULL && n_variants != NULL && n_units != NULL && units != NULL &&
              parity_floats(&t[2], status, n) && parity_floats(&t[3], n_variants, n) &&
              parity_floats(&t[4], n_units, n * LANG_VI_VARIANTS_MAX) &&
              parity_floats(&t[5], units, n * per_line);
    size_t offset = 0;
    for (size_t i = 0; ok && i < n; i++) {
        const char *item = next_text(&t[0], &offset);
        lang_vi_pron_t pron = {0};
        ok = item != NULL;
        const esp_err_t err =
            ok ? lang_vi_lexicon_entry(item, (lang_vi_dialect_t)((const uint8_t *)t[1].data)[i], &pron)
               : ESP_FAIL;
        status[n + i] = (float)err;
        n_variants[n + i] = pron.n_variants;
        for (size_t v = 0; v < LANG_VI_VARIANTS_MAX; v++) {
            n_units[n * LANG_VI_VARIANTS_MAX + i * LANG_VI_VARIANTS_MAX + v] = pron.n_units[v];
            for (size_t j = 0; j < LANG_VI_UNITS_MAX; j++) {
                units[n * per_line + i * per_line + v * LANG_VI_UNITS_MAX + j] = pron.units[v][j];
            }
        }
    }
    if (ok) {
        parity_report("lexicon", case_name, "status", status, status + n, n);
        parity_report("lexicon", case_name, "n_variants", n_variants, n_variants + n, n);
        parity_report("lexicon", case_name, "n_units", n_units, n_units + n * LANG_VI_VARIANTS_MAX,
                      n * LANG_VI_VARIANTS_MAX);
        parity_report("lexicon", case_name, "units", units, units + n * per_line, n * per_line);
    }
    free(units);
    free(n_units);
    free(n_variants);
    free(status);
    return ok;
}
