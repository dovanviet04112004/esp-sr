#include "test_report.h"

#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef ESP_PLATFORM
#include "driver/uart.h"
#include "driver/uart_vfs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "sdkconfig.h"
#endif

#define LINE_BYTES 192
#define TAG_BYTES 16
#define COMMAND_BYTES 512
#define UART_RX_BYTES 1024
#define CRC32_POLY 0xEDB88320u // reflected IEEE polynomial, as zlib.crc32

static struct {
    char tag[TAG_BYTES];
    char (*lines)[LINE_BYTES]; // "<seq> <text>", what the CRC covers
    size_t max_lines;
    size_t count;
} s_report;

static uint32_t crc32_of(const char *s)
{
    uint32_t crc = 0xFFFFFFFFu;
    for (; *s != '\0'; s++) {
        crc ^= (uint8_t)*s;
        for (int bit = 0; bit < 8; bit++) {
            crc = (crc >> 1) ^ (CRC32_POLY & (0u - (crc & 1u)));
        }
    }
    return ~crc;
}

static void print_kept(const char *body)
{
    printf("%s %s crc=%08x\n", s_report.tag, body, (unsigned)crc32_of(body));
    fflush(stdout);
}

bool test_report_begin(const char *tag, size_t max_lines)
{
    if (s_report.lines != NULL || max_lines == 0) { return false; }
    s_report.lines = calloc(max_lines, LINE_BYTES);
    if (s_report.lines == NULL) { return false; }
    snprintf(s_report.tag, sizeof(s_report.tag), "%s", tag);
    s_report.max_lines = max_lines;
    s_report.count = 0;
    return true;
}

static void add_line(const char *text)
{
    char body[LINE_BYTES];
    snprintf(body, sizeof(body), "%u %s", (unsigned)s_report.count, text);
    if (s_report.count < s_report.max_lines) { memcpy(s_report.lines[s_report.count], body, sizeof(body)); }
    s_report.count++;
    print_kept(body);
}

void test_report_line(const char *fmt, ...)
{
    char text[LINE_BYTES];
    va_list args;
    va_start(args, fmt);
    vsnprintf(text, sizeof(text), fmt, args);
    va_end(args);
    add_line(text);
}

#ifdef ESP_PLATFORM
static void resend(const char *word)
{
    const size_t kept = s_report.count < s_report.max_lines ? s_report.count : s_report.max_lines;
    if (strcmp(word, "end") == 0) {
        print_kept(s_report.lines[kept - 1]);
        return;
    }
    char *tail = NULL;
    const unsigned long seq = strtoul(word, &tail, 10);
    if (tail != word && *tail == '\0' && seq < kept) { print_kept(s_report.lines[seq]); }
}
#endif

void test_report_serve(void)
{
    char text[LINE_BYTES];
    snprintf(text, sizeof(text), "end %u lines", (unsigned)(s_report.count + 1));
    add_line(text);
#ifdef ESP_PLATFORM
    ESP_ERROR_CHECK(uart_driver_install(CONFIG_ESP_CONSOLE_UART_NUM, UART_RX_BYTES, 0, 0, NULL, 0));
    uart_vfs_dev_use_driver(CONFIG_ESP_CONSOLE_UART_NUM);
    char command[COMMAND_BYTES];
    for (;;) {
        if (fgets(command, sizeof(command), stdin) == NULL) {
            vTaskDelay(1);
            continue;
        }
        char *save = NULL;
        const char *verb = strtok_r(command, " \r\n", &save);
        if (verb == NULL || strcmp(verb, "resend") != 0) { continue; }
        for (const char *word = strtok_r(NULL, " \r\n", &save); word != NULL;
             word = strtok_r(NULL, " \r\n", &save)) {
            resend(word);
        }
    }
#else
    free(s_report.lines);
    s_report.lines = NULL;
#endif
}
