/** esp_sha on the host through OpenSSL, for the model image digests.
 *  @ctx any | non-blocking
 */
#pragma once

#include <stddef.h>

#include <openssl/sha.h>

typedef enum {
    SHA2_256 = 2,
} esp_sha_type;

static inline void esp_sha(esp_sha_type sha_type, const unsigned char *input, size_t ilen,
                           unsigned char *output)
{
    (void)sha_type;
    SHA256(input, ilen, output);
}
