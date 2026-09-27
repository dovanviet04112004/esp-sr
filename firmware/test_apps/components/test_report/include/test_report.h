/** Result lines a test app sends the host, each "<tag> <seq> <text> crc=<8 hex>" with the CRC32 of "<seq>
 * <text>", kept so the host can ask for any of them again (KEHOACH 4.5.7). test_apps/test_report.py is the
 * other end.
 *  @ctx task | blocking on the console | one report per app, not thread safe
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Start a report of at most max_lines lines, the end line included.
 *  @ctx task | blocking | allocates the kept lines
 *  @ret false when memory runs out or a report is already open
 */
bool test_report_begin(const char *tag, size_t max_lines);

/** Print and keep one line, printf style, without a newline; lines past max_lines are printed unkept.
 *  @ctx task | blocking on the console
 */
void test_report_line(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

/** Print "end <n> lines", then, on the board, answer "resend <seq> ..." and "resend end" lines for ever.
 *  @ctx task | never returns on the board; returns on the host, closing the report
 */
void test_report_serve(void);

#ifdef __cplusplus
}
#endif
