#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "lang_vi.h"

// Neutral shell of E3-T4: text is copied as it is until E11-T4 writes the rules (KEHOACH 3.12).

#define CODEPOINT_MAX 0x10FFFFu
#define SURROGATE_FIRST 0xD800u
#define SURROGATE_LAST 0xDFFFu

// Well-formed UTF-8 as RFC 3629 defines it: shortest form, no surrogates, nothing past U+10FFFF.
static bool is_utf8(const char *text)
{
    static const uint32_t kMinOfLength[] = {0, 0, 0x80u, 0x800u, 0x10000u};
    const uint8_t *p = (const uint8_t *)text;
    while (*p != 0) {
        const size_t length = *p < 0x80u           ? 1
                              : (*p >> 5) == 0x6u  ? 2
                              : (*p >> 4) == 0xEu  ? 3
                              : (*p >> 3) == 0x1Eu ? 4
                                                   : 0;
        if (length == 0) { return false; }
        uint32_t cp = length == 1 ? *p : *p & (0x7Fu >> length);
        for (size_t i = 1; i < length; i++) {
            if ((p[i] & 0xC0u) != 0x80u) { return false; }
            cp = (cp << 6) | (p[i] & 0x3Fu);
        }
        if (length > 1 && (cp < kMinOfLength[length] || cp > CODEPOINT_MAX ||
                           (cp >= SURROGATE_FIRST && cp <= SURROGATE_LAST))) {
            return false;
        }
        p += length;
    }
    return true;
}

esp_err_t lang_vi_normalize(const char *text, char *out, size_t cap)
{
    if (text == NULL || out == NULL || !is_utf8(text)) { return ESP_ERR_INVALID_ARG; }
    const size_t len = strlen(text);
    if (len >= cap) { return ESP_ERR_INVALID_SIZE; }
    memcpy(out, text, len + 1);
    return ESP_OK;
}
