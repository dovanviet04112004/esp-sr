#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "ai_engine.h"
#include "fake_storage.h"
#include "gen_grid.h"
#include "image_probe.h"
#include "sha/sha_core.h"
#include "storage_format.h"

#define NET_BYTES 100
#define NORM_BYTES 40

static unsigned s_failures;
static _Alignas(STORAGE_MODEL_ALIGN_BYTES)
    uint8_t s_image[STORAGE_MODEL_HEADER_BYTES + 2 * STORAGE_MODEL_ALIGN_BYTES * 2];

static void check(bool ok, const char *what)
{
    printf("HOST %s: %s\n", ok ? "PASS" : "FAIL", what);
    s_failures += ok ? 0u : 1u;
}

static void add_entry(storage_model_header_t *header, const char *name, uint32_t kind, uint32_t offset,
                      uint32_t size)
{
    storage_model_entry_t *entry = &header->entry[header->count++];
    strncpy(entry->name, name, sizeof(entry->name));
    entry->offset = offset;
    entry->size = size;
    entry->kind = kind;
    for (uint32_t i = 0; i < size; i++) {
        s_image[offset + i] = (uint8_t)(kind * 31u + i);
    }
    esp_sha(SHA2_256, &s_image[offset], size, entry->sha256);
}

static void build_image(uint32_t grid_hash)
{
    memset(s_image, 0, sizeof(s_image));
    storage_model_header_t *header = (storage_model_header_t *)s_image;
    header->magic = STORAGE_MODEL_MAGIC;
    header->format_ver = STORAGE_MODEL_FORMAT_VER;
    header->grid_hash = grid_hash;
    add_entry(header, "wake", STORAGE_MODEL_KIND_ESPDL, STORAGE_MODEL_HEADER_BYTES, NET_BYTES);
    add_entry(header, "wake", STORAGE_MODEL_KIND_NORM,
              STORAGE_MODEL_HEADER_BYTES + 2 * STORAGE_MODEL_ALIGN_BYTES, NORM_BYTES);
    fake_storage_set_image(s_image, sizeof(s_image));
}

static void check_loader(void)
{
    fake_storage_set_image(NULL, 0);
    check(ai_engine_load(0) == ESP_ERR_NOT_FOUND && image_probe_bytes() == 0, "an empty slot loads nothing");

    build_image(GEN_GRID_HASH ^ 1u);
    check(ai_engine_load(0) == ESP_ERR_INVALID_VERSION && image_probe_bytes() == 0,
          "an image learned on another grid is refused whole");

    build_image(GEN_GRID_HASH);
    s_image[STORAGE_MODEL_HEADER_BYTES + 2 * STORAGE_MODEL_ALIGN_BYTES + 7] ^= 0x01;
    check(ai_engine_load(1) == ESP_ERR_INVALID_CRC && image_probe_bytes() == 0,
          "one flipped bit in the second entry fails its sha256 and drops the first too");

    build_image(GEN_GRID_HASH);
    size_t net_bytes = 0;
    size_t norm_bytes = 0;
    size_t units_bytes = 7;
    check(ai_engine_load(1) == ESP_OK, "a sound image on this grid loads");
    const uint8_t *net = image_probe_find("wake", STORAGE_MODEL_KIND_ESPDL, &net_bytes);
    const uint8_t *norm = image_probe_find("wake", STORAGE_MODEL_KIND_NORM, &norm_bytes);
    const uint8_t *units = image_probe_find("wake", STORAGE_MODEL_KIND_UNITS, &units_bytes);
    check(net != NULL && net_bytes == NET_BYTES &&
              memcmp(net, &s_image[STORAGE_MODEL_HEADER_BYTES], NET_BYTES) == 0 && (uintptr_t)net % 16 == 0 &&
              net != &s_image[STORAGE_MODEL_HEADER_BYTES],
          "an entry is found by name and kind as an aligned copy of its bytes");
    check(norm != NULL && norm_bytes == NORM_BYTES && units == NULL && units_bytes == 0 &&
              image_probe_find("command", STORAGE_MODEL_KIND_ESPDL, &units_bytes) == NULL,
          "another kind or name finds nothing");
    check(image_probe_bytes() == NET_BYTES + NORM_BYTES, "the PSRAM held is the sum of the entries");

    fake_storage_set_image(NULL, 0);
    check(ai_engine_load(0) == ESP_ERR_NOT_FOUND && image_probe_bytes() == 0,
          "loading again drops the image held, even when the new slot is empty");
}

static void check_shells(void)
{
    static const float log_mel[40];
    static int16_t pcm[16];
    static const uint8_t units[3] = {1, 2, 3};
    static ai_engine_lexicon_t lexicon;
    uint16_t score = 7;
    size_t n_samples = 9;
    ai_engine_command_result_t result;
    bool none = true;
    for (int m = 0; m < AI_ENGINE_MODEL_COUNT; m++) {
        none = none && !ai_engine_has((ai_engine_model_t)m);
    }
    check(none, "no branch builds a network yet, so no model is present");
    check(ai_engine_ns_ops() == NULL, "ns shell: nothing to plug into the dsp_afe slot");
    ai_engine_wake_reset();
    check(ai_engine_wake_step(log_mel, &score) == ESP_ERR_INVALID_STATE &&
              ai_engine_wake_step(NULL, &score) == ESP_ERR_INVALID_ARG,
          "wake shell: no wake model, arguments still checked");
    check(ai_engine_command_prepare(&lexicon) == ESP_ERR_INVALID_STATE &&
              ai_engine_command_begin() == ESP_ERR_INVALID_STATE &&
              ai_engine_command_step(log_mel) == ESP_ERR_INVALID_STATE &&
              ai_engine_command_score(&lexicon, &result) == ESP_ERR_INVALID_STATE &&
              ai_engine_command_abort() == ESP_ERR_INVALID_STATE,
          "command shell: no command model, so no window ever opens");
    check(ai_engine_synth_render(units, 3, pcm, 16, &n_samples) == ESP_ERR_NOT_SUPPORTED &&
              ai_engine_synth_render(units, 3, pcm, 16, NULL) == ESP_ERR_INVALID_ARG,
          "synth shell: no synth in the image");
}

int main(void)
{
    check_loader();
    check_shells();
    printf("HOST %u failure(s)\n", s_failures);
    return s_failures == 0 ? 0 : 1;
}
