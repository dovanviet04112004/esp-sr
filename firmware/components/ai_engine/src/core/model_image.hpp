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
    const uint8_t *data; // PSRAM, 16-byte aligned, valid until the next image_load
    size_t size;
};

/** Drop the loaded image, then copy slot's; after a failure no image is loaded.
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND | ESP_ERR_INVALID_VERSION grid or format | ESP_ERR_INVALID_SIZE
 *       | ESP_ERR_INVALID_CRC | ESP_ERR_NO_MEM
 */
esp_err_t image_load(uint8_t slot) noexcept;

/** The loaded entry of that name and kind; data is nullptr when the image has none. */
Blob image_find(const char *name, storage_model_kind_t kind) noexcept;

/** PSRAM bytes the loaded entries hold. */
size_t image_bytes() noexcept;

/** Whether the loaded image's command learned on the listen.yaml this build cuts windows by (KEHOACH 6.3);
 * logs both hashes under name when not. */
bool image_listens_as_built(const char *name) noexcept;

} // namespace ai
