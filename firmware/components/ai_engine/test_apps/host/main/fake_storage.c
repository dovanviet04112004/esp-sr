#include "fake_storage.h"

#include "sys_storage.h"

static const storage_model_header_t *s_image;
static size_t s_bytes;

void fake_storage_set_image(const void *image, size_t bytes)
{
    s_image = image;
    s_bytes = bytes;
}

esp_err_t sys_storage_map_models(uint8_t slot, sys_storage_models_t *out)
{
    if (out == NULL || slot > 1) { return ESP_ERR_INVALID_ARG; }
    if (s_image == NULL || s_image->magic != STORAGE_MODEL_MAGIC) { return ESP_ERR_NOT_FOUND; }
    out->header = s_image;
    out->partition_bytes = s_bytes;
    out->unmap_handle = 0;
    return ESP_OK;
}

void sys_storage_unmap_models(sys_storage_models_t *models)
{
    models->header = NULL;
}
