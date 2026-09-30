#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "ai_engine.h"
#include "esp_heap_caps.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "gen_grid.h"
#include "sha/sha_core.h"
#include "storage_format.h"
#include "sys_storage.h"
#include "unity.h"

#define SECTOR_BYTES 4096
#define BIG_BYTES (1024 * 1024) // the order of a command network (KEHOACH 6.6)
#define SMALL_BYTES 4000
#define BIG_OFFSET SECTOR_BYTES
#define SMALL_OFFSET (BIG_OFFSET + BIG_BYTES)
#define PSRAM_SLACK_BYTES 1024 // heap headers and alignment of two blocks

static storage_model_header_t s_header;
static uint8_t s_small[SMALL_BYTES];

static const esp_partition_t *slot1(void)
{
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                                           STORAGE_MODEL_LABEL_SLOT1);
    TEST_ASSERT_NOT_NULL(part);
    return part;
}

static void fill(uint8_t *bytes, size_t n, uint32_t seed)
{
    for (size_t i = 0; i < n; i++) {
        seed ^= seed << 13;
        seed ^= seed >> 17;
        seed ^= seed << 5;
        bytes[i] = (uint8_t)seed;
    }
}

static void set_entry(int i, const char *name, uint32_t kind, uint32_t offset, const uint8_t *bytes,
                      uint32_t size)
{
    storage_model_entry_t *entry = &s_header.entry[i];
    strncpy(entry->name, name, sizeof(entry->name));
    entry->offset = offset;
    entry->size = size;
    entry->kind = kind;
    esp_sha(SHA2_256, bytes, size, entry->sha256);
}

static void write_header(const esp_partition_t *part, uint32_t grid_hash)
{
    s_header.grid_hash = grid_hash;
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, SECTOR_BYTES));
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_write(part, 0, &s_header, sizeof(s_header)));
}

static void write_small(const esp_partition_t *part, bool flip_one_bit)
{
    s_small[SMALL_BYTES / 2] ^= flip_one_bit ? 0x10 : 0x00;
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, SMALL_OFFSET, SECTOR_BYTES));
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_write(part, SMALL_OFFSET, s_small, SMALL_BYTES));
    s_small[SMALL_BYTES / 2] ^= flip_one_bit ? 0x10 : 0x00;
}

TEST_CASE("a model slot loads only when its grid and every sha256 match", "[ai_engine]")
{
    const esp_partition_t *part = slot1();
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, SECTOR_BYTES));
    TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND, ai_engine_load(1));

    uint8_t *big = heap_caps_malloc(BIG_BYTES, MALLOC_CAP_SPIRAM);
    TEST_ASSERT_NOT_NULL(big);
    fill(big, BIG_BYTES, 0x2545F491u);
    fill(s_small, SMALL_BYTES, 0x9E3779B9u);
    memset(&s_header, 0, sizeof(s_header));
    s_header.magic = STORAGE_MODEL_MAGIC;
    s_header.format_ver = STORAGE_MODEL_FORMAT_VER;
    s_header.count = 2;
    set_entry(0, "command", STORAGE_MODEL_KIND_ESPDL, BIG_OFFSET, big, BIG_BYTES);
    set_entry(1, "command", STORAGE_MODEL_KIND_UNITS, SMALL_OFFSET, s_small, SMALL_BYTES);
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, BIG_OFFSET, BIG_BYTES));
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_write(part, BIG_OFFSET, big, BIG_BYTES));
    heap_caps_free(big);
    write_small(part, false);

    const size_t psram_free = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    write_header(part, GEN_GRID_HASH ^ 1u);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_VERSION, ai_engine_load(1));
    TEST_ASSERT_EQUAL(psram_free, heap_caps_get_free_size(MALLOC_CAP_SPIRAM));

    write_header(part, GEN_GRID_HASH);
    write_small(part, true);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_CRC, ai_engine_load(1));
    TEST_ASSERT_EQUAL(psram_free, heap_caps_get_free_size(MALLOC_CAP_SPIRAM));

    write_small(part, false);
    const int64_t started_us = esp_timer_get_time();
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_load(1));
    const int64_t load_us = esp_timer_get_time() - started_us;
    const size_t held = psram_free - heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    printf("ai_engine_load: %u KB in %" PRId64 " ms, %.1f MB/s, PSRAM held %u KB\n",
           (unsigned)((BIG_BYTES + SMALL_BYTES) / 1024), load_us / 1000,
           (double)(BIG_BYTES + SMALL_BYTES) / (double)load_us, (unsigned)(held / 1024));
    TEST_ASSERT_UINT32_WITHIN(PSRAM_SLACK_BYTES, BIG_BYTES + SMALL_BYTES, held);

    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, SECTOR_BYTES));
    TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND, ai_engine_load(1));
    TEST_ASSERT_EQUAL(psram_free, heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
}

void app_main(void)
{
    ESP_ERROR_CHECK(sys_storage_init());
    UNITY_BEGIN();
    // The ns probe reads models_1 as make ai-unit wrote it; every other case rewrites that slot.
    unity_run_tests_by_tag("[ns_probe]", false);
    unity_run_tests_by_tag("[ns_probe]", true);
    UNITY_END();
}
