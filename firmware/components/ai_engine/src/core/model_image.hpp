/** The model slot copied into PSRAM (KEHOACH 6.3, 6.5): every entry of a header on this build's grid, each
 * copy checked against its sha256, then found by name and kind. Knows no model by name.
 *  @ctx task | image_load blocks on flash for seconds, once ahead of any step; the rest is non-blocking
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "storage_format.h"

namespace ai {

struct Blob {
    const uint8_t *data; // 16-byte aligned in PSRAM or flash, until the next load
    size_t size;
};

/** Drop the loaded image, then copy slot's; after a failure no image is loaded.
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND | ESP_ERR_INVALID_VERSION grid or format | ESP_ERR_INVALID_SIZE
 *       | ESP_ERR_INVALID_CRC | ESP_ERR_NO_MEM
 */
esp_err_t image_load(uint8_t slot) noexcept;

/** The loaded entry of that name and kind; data is nullptr when the image has none. */
Blob image_find(const char *name, storage_model_kind_t kind) noexcept;

/** PSRAM bytes the loaded entries hold; an entry left in flash holds none. */
size_t image_bytes() noexcept;

/** The first four bytes of the sha256 of the entry of that name and kind, big-endian; 0 when the image has
 * none. */
uint32_t image_entry_id(const char *name, storage_model_kind_t kind) noexcept;

/** The threshold of the image's speaker row with that many voiceprints held, thousandths of the AS-norm
 * score; 0 outside 1..APP_SPEAKERS_MAX (KEHOACH 3.17, 6.3). */
int16_t image_speaker_threshold_milli_z(size_t prints) noexcept;

/** The cohort rows AS-norm averages over at each side, as the image's header gives it; 0 without one. */
uint16_t image_speaker_cohort_top() noexcept;

/** The image's STORAGE_SPK_NOISE_EDGES noise edges, rising utterance-over-lead power ratios, 0 without them
 * (ADR-0024). */
const float *image_speaker_noise_edges() noexcept;

/** The image's thresholds in the noisier bins: APP_SPEAKERS_MAX rows, by voiceprints held, of
 * STORAGE_SPK_NOISE_EDGES, thousandths of the AS-norm score (ADR-0024). */
const int16_t *image_speaker_noisy_milli_z() noexcept;

/** Whether the loaded image's speaker came with the speaker.yaml this build runs (KEHOACH 3.17); logs
 * both hashes under name when not. */
bool image_speaks_as_built(const char *name) noexcept;

/** Whether the loaded image's command learned on the listen.yaml this build cuts windows by (KEHOACH 6.3);
 * logs both hashes under name when not. */
bool image_listens_as_built(const char *name) noexcept;

} // namespace ai
