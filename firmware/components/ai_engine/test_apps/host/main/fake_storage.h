/** sys_storage_map_models over an image the test builds in memory, for both slots.
 *  @ctx test only | non-blocking | image must outlive every load; NULL empties the slots
 */
#pragma once

#include <stddef.h>

void fake_storage_set_image(const void *image, size_t bytes);
