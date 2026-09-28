#include "core/model_image.hpp"

#include <inttypes.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "gen_grid.h"
#include "sha/sha_core.h"
#include "sys_storage.h"

namespace ai {

namespace {

const char *const TAG = "ai_image";
// esp-dl reads parameters with 16-byte vector loads.
constexpr size_t kAlignBytes = 16;

struct Entry {
    char name[STORAGE_MODEL_NAME_BYTES];
    uint32_t kind;
    uint8_t *data;
    size_t size;
};

Entry s_entries[STORAGE_MODEL_MAX_ENTRIES];
size_t s_count;

void release() noexcept
{
    for (size_t i = 0; i < s_count; ++i) {
        heap_caps_free(s_entries[i].data);
    }
    memset(s_entries, 0, sizeof(s_entries));
    s_count = 0;
}

esp_err_t copy_entries(const sys_storage_models_t &models) noexcept
{
    const auto *base = reinterpret_cast<const uint8_t *>(models.header);
    for (uint32_t i = 0; i < models.header->count; ++i) {
        const storage_model_entry_t &from = models.header->entry[i];
        auto *copy =
            static_cast<uint8_t *>(heap_caps_aligned_alloc(kAlignBytes, from.size, MALLOC_CAP_SPIRAM));
        if (copy == nullptr) { return ESP_ERR_NO_MEM; }
        Entry &to = s_entries[s_count++];
        memcpy(to.name, from.name, sizeof(to.name));
        to.kind = from.kind;
        to.data = copy;
        to.size = from.size;
        memcpy(copy, base + from.offset, from.size);
        uint8_t digest[STORAGE_MODEL_SHA256_BYTES];
        // Hashing the copy catches a bad flash read and a bad copy alike.
        esp_sha(SHA2_256, copy, from.size, digest);
        if (memcmp(digest, from.sha256, sizeof(digest)) != 0) {
            ESP_LOGE(TAG, "%.*s: sha256 differs from the header", STORAGE_MODEL_NAME_BYTES, from.name);
            return ESP_ERR_INVALID_CRC;
        }
    }
    return ESP_OK;
}

} // namespace

esp_err_t image_load(uint8_t slot) noexcept
{
    release();
    sys_storage_models_t models = {};
    esp_err_t err = sys_storage_map_models(slot, &models);
    if (err != ESP_OK) { return err; }
    if (models.header->grid_hash != GEN_GRID_HASH) {
        ESP_LOGE(TAG, "slot %u learned on grid 0x%08" PRIx32 ", this build runs 0x%08" PRIx32, slot,
                 models.header->grid_hash, (uint32_t)GEN_GRID_HASH);
        err = ESP_ERR_INVALID_VERSION;
    } else {
        err = copy_entries(models);
    }
    sys_storage_unmap_models(&models);
    if (err != ESP_OK) {
        release();
        return err;
    }
    ESP_LOGI(TAG, "slot %u: %u entries, %u KB in PSRAM", slot, (unsigned)s_count,
             (unsigned)(image_bytes() / 1024));
    return ESP_OK;
}

Blob image_find(const char *name, storage_model_kind_t kind) noexcept
{
    for (size_t i = 0; i < s_count; ++i) {
        const Entry &e = s_entries[i];
        if (e.kind == (uint32_t)kind && strncmp(e.name, name, sizeof(e.name)) == 0) {
            return {e.data, e.size};
        }
    }
    return {nullptr, 0};
}

size_t image_bytes() noexcept
{
    size_t total = 0;
    for (size_t i = 0; i < s_count; ++i) {
        total += s_entries[i].size;
    }
    return total;
}

} // namespace ai
