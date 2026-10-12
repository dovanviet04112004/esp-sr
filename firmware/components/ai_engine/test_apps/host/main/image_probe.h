/** The loaded image seen from the C test, through the private core of ai_engine.
 *  @ctx test only | non-blocking
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

const uint8_t *image_probe_find(const char *name, uint32_t kind, size_t *size);
size_t image_probe_bytes(void);
bool image_probe_listens(const char *name);
bool image_probe_speaks(const char *name);
int16_t image_probe_speaker_threshold(size_t prints);
uint16_t image_probe_cohort_top(void);
float image_probe_noise_edge(size_t k);
int16_t image_probe_noisy_threshold(size_t prints, size_t bin);
uint32_t image_probe_id(const char *name, uint32_t kind);

#ifdef __cplusplus
}
#endif
