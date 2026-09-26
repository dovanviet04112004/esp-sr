#include <math.h>
#include <stdio.h>
#include <string.h>

#include "parity.h"

#define SNR_NO_ERROR_DB 999.0

bool parity_tensor(const void *buf, size_t len, const char *name, gold_tensor_t *out)
{
    gold_reader_t reader;
    if (!gold_open(&reader, buf, len)) { return false; }
    while (gold_next(&reader, out)) {
        if (strcmp(out->name, name) == 0) { return true; }
    }
    return false;
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
