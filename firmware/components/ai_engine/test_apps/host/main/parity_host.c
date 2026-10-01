#include <stdint.h>
#include <stdio.h>

#include "parity.h"
#include "test_report.h"

#define CASE_BYTES_MAX (512 * 1024)
#define REPORT_LINES_MAX 64

static bool read_case(const char *path, void *buf, size_t cap, size_t *len)
{
    FILE *file = fopen(path, "rb");
    if (file == NULL) { return false; }
    *len = fread(buf, 1, cap, file);
    const bool whole = feof(file) != 0;
    fclose(file);
    return whole;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s <contracts/golden>\n", argv[0]);
        return 2;
    }
    static uint8_t buf[CASE_BYTES_MAX];
    unsigned errors = 0;
    test_report_begin("PARITY", REPORT_LINES_MAX);
    unsigned cases =
        parity_run_block(argv[1], "command_kws", parity_command_kws, read_case, buf, sizeof(buf), &errors);
    cases +=
        parity_run_block(argv[1], "command_ctc", parity_command_ctc, read_case, buf, sizeof(buf), &errors);
    test_report_line("done %u cases", cases);
    test_report_serve();
    printf("HOST %u failure(s)\n", errors);
    return errors == 0 ? 0 : 1;
}
