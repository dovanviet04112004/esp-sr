/** The loaded image seen from the C test, through the private core of ai_engine.
 *  @ctx test only | non-blocking
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

const uint8_t *image_probe_find(const char *name, uint32_t kind, size_t *size);
size_t image_probe_bytes(void);

#ifdef __cplusplus
}
#endif
