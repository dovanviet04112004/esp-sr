#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "lang_vi.h"

#define LANG_CODEPOINT_BAD 0xFFFFFFFFu

// Next code point of well-formed UTF-8 (RFC 3629) at *p, which it advances; LANG_CODEPOINT_BAD otherwise.
uint32_t lang_next_codepoint(const char **p);

bool lang_is_utf8(const char *text);

// Lower-case base and tone index of a precomposed Vietnamese vowel, an ASCII vowel or đ, in either case.
bool lang_letter_of(uint32_t codepoint, uint32_t *base, uint8_t *tone);

bool lang_is_vowel_base(uint32_t base);

// The lower-case code point of base in a tone; a consonant is its own form.
uint32_t lang_letter_form(uint32_t base, uint8_t tone);

// Appends codepoint as UTF-8 at out + *len while it fits cap with a NUL after it.
bool lang_put_codepoint(char *out, size_t cap, size_t *len, uint32_t codepoint);

// lang_vi_normalize reading numbers as the dialect at index dialect of lang_vi.yaml reads them.
esp_err_t lang_normalize_for(const char *text, size_t dialect, char *out, size_t cap);
