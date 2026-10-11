#include "core/model_image.hpp"

#include <inttypes.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "gen_grid.h"
#include "gen_listen.h"
#include "gen_speaker.h"
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
    const uint8_t *data;
    size_t size;
    bool in_flash; // read through the mapping, never copied
    uint32_t id;   // first four bytes of its sha256
};

Entry s_entries[STORAGE_MODEL_MAX_ENTRIES];
size_t s_count;
uint32_t s_listen_hash;
uint32_t s_speaker_hash;
int16_t s_speaker_thresholds[APP_SPEAKERS_MAX];
sys_storage_models_t s_mapping; // held while an entry stays in flash

void release() noexcept
{
    for (size_t i = 0; i < s_count; ++i) {
        if (!s_entries[i].in_flash) { heap_caps_free(const_cast<uint8_t *>(s_entries[i].data)); }
    }
    if (s_mapping.header != nullptr) { sys_storage_unmap_models(&s_mapping); }
    memset(s_entries, 0, sizeof(s_entries));
    s_mapping = {};
    s_count = 0;
    s_listen_hash = 0;
    s_speaker_hash = 0;
    memset(s_speaker_thresholds, 0, sizeof(s_speaker_thresholds));
}

uint32_t id_of(const uint8_t *sha256) noexcept
{
    return (uint32_t)sha256[0] << 24 | (uint32_t)sha256[1] << 16 | (uint32_t)sha256[2] << 8 | sha256[3];
}

// An entry flagged to stay in flash is read in place: b0 gives up 3.8% of speed for 1.72 MB of PSRAM
// (KEHOACH 6.5).
esp_err_t copy_entries(const sys_storage_models_t &models) noexcept
{
    const auto *base = reinterpret_cast<const uint8_t *>(models.header);
    for (uint32_t i = 0; i < models.header->count; ++i) {
        const storage_model_entry_t &from = models.header->entry[i];
        const bool in_flash = (from.flags & STORAGE_MODEL_FLAG_IN_FLASH) != 0;
        const uint8_t *data = base + from.offset;
        if (!in_flash) {
            auto *copy =
                static_cast<uint8_t *>(heap_caps_aligned_alloc(kAlignBytes, from.size, MALLOC_CAP_SPIRAM));
            if (copy == nullptr) { return ESP_ERR_NO_MEM; }
            memcpy(copy, data, from.size);
            data = copy;
        }
        Entry &to = s_entries[s_count++];
        memcpy(to.name, from.name, sizeof(to.name));
        to.kind = from.kind;
        to.data = data;
        to.size = from.size;
        to.in_flash = in_flash;
        to.id = id_of(from.sha256);
        uint8_t digest[STORAGE_MODEL_SHA256_BYTES];
        // Hashing what the model reads catches a bad flash read and a bad copy alike.
        esp_sha(SHA2_256, data, from.size, digest);
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
        s_listen_hash = models.header->listen_hash;
        s_speaker_hash = models.header->speaker_hash;
        memcpy(s_speaker_thresholds, models.header->spk_accept_permille, sizeof(s_speaker_thresholds));
    }
    bool held = false;
    for (size_t i = 0; i < s_count; ++i) {
        held = held || s_entries[i].in_flash;
    }
    if (held && err == ESP_OK) {
        s_mapping = models;
    } else {
        sys_storage_unmap_models(&models);
    }
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
        total += s_entries[i].in_flash ? 0 : s_entries[i].size;
    }
    return total;
}

uint32_t image_entry_id(const char *name, storage_model_kind_t kind) noexcept
{
    for (size_t i = 0; i < s_count; ++i) {
        const Entry &e = s_entries[i];
        if (e.kind == (uint32_t)kind && strncmp(e.name, name, sizeof(e.name)) == 0) { return e.id; }
    }
    return 0;
}

int16_t image_speaker_threshold_permille(size_t prints) noexcept
{
    return prints >= 1 && prints <= APP_SPEAKERS_MAX ? s_speaker_thresholds[prints - 1] : 0;
}

bool image_speaks_as_built(const char *name) noexcept
{
    if (s_speaker_hash == GEN_SPEAKER_HASH) { return true; }
    ESP_LOGE(TAG, "%s imported on speaker.yaml 0x%08" PRIx32 ", this build runs 0x%08" PRIx32, name,
             s_speaker_hash, (uint32_t)GEN_SPEAKER_HASH);
    return false;
}

bool image_listens_as_built(const char *name) noexcept
{
    if (s_listen_hash == GEN_LISTEN_HASH) { return true; }
    ESP_LOGE(TAG, "%s learned on listen.yaml 0x%08" PRIx32 ", this build cuts by 0x%08" PRIx32, name,
             s_listen_hash, (uint32_t)GEN_LISTEN_HASH);
    return false;
}

} // namespace ai
