#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ai_engine.h"
#include "fake_storage.h"
#include "gen_grid.h"
#include "gold_read.h"
#include "sha/sha_core.h"
#include "storage_format.h"

#define CASE_BYTES_MAX (1024 * 1024)
#define BINS GEN_GRID_N_BINS

static unsigned s_failures;
static uint8_t s_case[CASE_BYTES_MAX];
static _Alignas(STORAGE_MODEL_ALIGN_BYTES) uint8_t s_image[STORAGE_MODEL_HEADER_BYTES + CASE_BYTES_MAX];

static void check(bool ok, const char *what)
{
    printf("HOST %s: %s\n", ok ? "PASS" : "FAIL", what);
    s_failures += ok ? 0u : 1u;
}

static bool find(size_t len, const char *name, gold_tensor_t *out)
{
    gold_reader_t r;
    if (!gold_open(&r, s_case, len)) { return false; }
    while (gold_next(&r, out)) {
        if (strcmp(out->name, name) == 0) { return true; }
    }
    return false;
}

// A model image of one WEIGHTS entry named entry, holding blob; an empty image when blob is NULL.
static void build_image(const char *entry, const void *blob, size_t bytes)
{
    memset(s_image, 0, STORAGE_MODEL_HEADER_BYTES);
    storage_model_header_t *header = (storage_model_header_t *)s_image;
    header->magic = STORAGE_MODEL_MAGIC;
    header->format_ver = STORAGE_MODEL_FORMAT_VER;
    header->grid_hash = GEN_GRID_HASH;
    if (blob != NULL) {
        storage_model_entry_t *e = &header->entry[header->count++];
        strncpy(e->name, entry, sizeof(e->name));
        e->offset = STORAGE_MODEL_HEADER_BYTES;
        e->size = (uint32_t)bytes;
        e->kind = STORAGE_MODEL_KIND_WEIGHTS;
        memcpy(&s_image[e->offset], blob, bytes);
        esp_sha(SHA2_256, &s_image[e->offset], bytes, e->sha256);
    }
    fake_storage_set_image(s_image, STORAGE_MODEL_HEADER_BYTES + bytes);
}

// The golden's bins through ai_engine_ns_ops() as dsp_afe calls it; true when every gain matches bit for bit.
static bool run_hops(const dsp_afe_ns_ops_t *ops, void *state, const gold_tensor_t *bins,
                     const gold_tensor_t *gain)
{
    float got[BINS];
    float speech_prob = 1.0f;
    bool same = true;
    for (uint32_t h = 0; h < bins->dims[0]; h++) {
        const float *in = (const float *)bins->data + (size_t)h * 2 * BINS;
        same = same && ops->process(NULL, state, in, NULL, got, &speech_prob) == ESP_OK;
        same = same && memcmp(got, (const float *)gain->data + (size_t)h * BINS, sizeof(got)) == 0;
    }
    return same && speech_prob == 0.0f;
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        printf("usage: %s <contracts/golden>\n", argv[0]);
        return 2;
    }
    char path[512];
    snprintf(path, sizeof(path), "%s/%s/case_000.gold", argv[1], NS_HOST_ENTRY);
    FILE *file = fopen(path, "rb");
    const size_t len = file != NULL ? fread(s_case, 1, sizeof(s_case), file) : 0;
    if (file != NULL) { fclose(file); }
    gold_tensor_t weights, bins, gain;
    if (!find(len, "weights", &weights) || !find(len, "bins", &bins) || !find(len, "gain", &gain)) {
        printf("HOST FAIL: %s does not hold weights, bins and gain\n", path);
        return 1;
    }
    build_image(NS_HOST_ENTRY, NULL, 0);
    check(ai_engine_load(1) == ESP_ERR_NOT_FOUND || ai_engine_ns_ops() == NULL,
          "an image without the net's entry leaves the slot to the floor");
    build_image(NS_HOST_ENTRY, weights.data, weights.dims[0]);
    check(ai_engine_load(1) == ESP_OK && ai_engine_has(AI_ENGINE_MODEL_NS) && ai_engine_ns_ops() != NULL,
          "the image's " NS_HOST_ENTRY " entry builds the ns branch");
    const dsp_afe_ns_ops_t *ops = ai_engine_ns_ops();
    if (ops == NULL) { return 1; }
    const size_t bytes = ops->state_bytes(NULL);
    void *state = malloc(bytes);
    check(ops->init(NULL, state, bytes) == ESP_OK && run_hops(ops, state, &bins, &gain),
          "the slot's gains match the mirror's bit for bit over the golden's hops");
    ops->reset(NULL, state);
    check(run_hops(ops, state, &bins, &gain), "reset gives the stream's first hop again");
    ((uint8_t *)s_image)[STORAGE_MODEL_HEADER_BYTES + 3] ^= 0xffu;
    esp_sha(SHA2_256, &s_image[STORAGE_MODEL_HEADER_BYTES], weights.dims[0],
            ((storage_model_header_t *)s_image)->entry[0].sha256);
    check(ai_engine_load(1) == ESP_OK && ai_engine_ns_ops() == NULL,
          "a blob of another magic leaves the slot to the floor");
    free(state);
    printf("HOST %u failure(s)\n", s_failures);
    return s_failures == 0 ? 0 : 1;
}
