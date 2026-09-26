#include <dirent.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "parity.h"

#define SNR_NO_ERROR_DB 999.0
#define DIR_BYTES 256
#define NAME_BYTES 256 // struct dirent d_name
#define PATH_BYTES (DIR_BYTES + 1 + NAME_BYTES)
#define GOLD_SUFFIX ".gold"

bool parity_tensor(const void *buf, size_t len, const char *name, gold_tensor_t *out)
{
    gold_reader_t reader;
    if (!gold_open(&reader, buf, len)) { return false; }
    while (gold_next(&reader, out)) {
        if (strcmp(out->name, name) == 0) { return true; }
    }
    return false;
}

bool parity_floats(const gold_tensor_t *t, float *out, size_t n)
{
    size_t count = t->ndim > 0 ? 1 : 0;
    for (uint32_t d = 0; d < t->ndim; d++) {
        count *= t->dims[d];
    }
    if (count != n) { return false; }
    for (size_t i = 0; i < n; i++) {
        switch (t->dtype) {
        case GOLD_F32: out[i] = ((const float *)t->data)[i]; break;
        case GOLD_I8: out[i] = ((const int8_t *)t->data)[i]; break;
        case GOLD_I32: out[i] = (float)((const int32_t *)t->data)[i]; break;
        case GOLD_U8: out[i] = ((const uint8_t *)t->data)[i]; break;
        case GOLD_I16: out[i] = ((const int16_t *)t->data)[i]; break;
        default: return false;
        }
    }
    return true;
}

void parity_report(const char *block, const char *case_name, const char *tensor, const float *want,
                   const float *got, size_t n)
{
    double reference = 0.0, error = 0.0, max_abs = 0.0;
    for (size_t i = 0; i < n; i++) {
        const double d = (double)got[i] - want[i];
        reference += (double)want[i] * want[i];
        error += d * d;
        max_abs = fmax(max_abs, fabs(d));
    }
    const double snr_db = error > 0.0 ? 10.0 * log10(reference / error) : SNR_NO_ERROR_DB;
    printf("PARITY %s %s %s max_abs=%.3e snr_db=%.1f\n", block, case_name, tensor, max_abs, snr_db);
}

unsigned parity_run_block(const char *root, const char *block, parity_runner_t run, parity_reader_t read,
                          void *buf, size_t cap, unsigned *errors)
{
    char dir_path[DIR_BYTES];
    snprintf(dir_path, sizeof(dir_path), "%s/%s", root, block);
    DIR *dir = opendir(dir_path);
    if (dir == NULL) {
        printf("PARITY missing %s\n", dir_path);
        return 0;
    }
    unsigned cases = 0;
    for (struct dirent *entry = readdir(dir); entry != NULL; entry = readdir(dir)) {
        const size_t name_len = strlen(entry->d_name);
        const size_t suffix_len = strlen(GOLD_SUFFIX);
        if (name_len <= suffix_len || strcmp(entry->d_name + name_len - suffix_len, GOLD_SUFFIX) != 0) {
            continue;
        }
        char path[PATH_BYTES];
        char case_name[NAME_BYTES];
        snprintf(path, sizeof(path), "%s/%s", dir_path, entry->d_name);
        snprintf(case_name, sizeof(case_name), "%.*s", (int)(name_len - suffix_len), entry->d_name);
        size_t len = 0;
        if (!read(path, buf, cap, &len) || !run(case_name, buf, len)) {
            printf("PARITY error %s %s\n", block, case_name);
            if (errors != NULL) { (*errors)++; }
            continue;
        }
        cases++;
    }
    closedir(dir);
    return cases;
}
