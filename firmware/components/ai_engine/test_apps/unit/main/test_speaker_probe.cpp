#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ai_engine.h"
#include "core/espdl_net.hpp"
#include "core/model_image.hpp"
#include "dl_model_base.hpp"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "storage_format.h"
#include "sys_storage.h"
#include "unity.h"

extern const uint8_t speaker_windows_start[] asm("_binary_speaker_windows_bin_start");

namespace {

constexpr size_t kSectorBytes = 4096;
constexpr int kToleranceSteps = 1; // one int8 step, as model->test() allows (KEHOACH 3.14)
constexpr uint32_t kShownValues = 8;
constexpr int kCutsMax = 16;

// Layout written by srpipe.tasks.speaker.probe; the record's int8 data is padded to four bytes.
struct WindowsHead {
    char magic[4];
    uint32_t runs;
};
struct WindowRecord {
    char entry[STORAGE_MODEL_NAME_BYTES];
    uint32_t frames;
    uint32_t mels;
    uint32_t dims;
    int32_t input_exponent;
    int32_t output_exponent;
};
static_assert(sizeof(WindowsHead) == 8 && sizeof(WindowRecord) == 36, "speaker_windows.bin layout");

esp_err_t test_of(const ai::Blob &blob, bool profiled)
{
    dl::Model model(reinterpret_cast<const char *>(blob.data), fbs::MODEL_LOCATION_IN_FLASH_RODATA, 0,
                    dl::MEMORY_MANAGER_GREEDY, nullptr, false);
    if (profiled) { model.profile_module(true); }
    return model.test();
}

} // namespace

TEST_CASE("the speaker graph embeds one window as its ESP-PPQ simulation does, timed", "[ai_engine][speaker]")
{
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(0),
                              "models_0 lacks speaker_models.bin: run make ai-unit-speaker");
    WindowsHead head;
    memcpy(&head, speaker_windows_start, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY("SRSV", head.magic, 4);
    TEST_ASSERT_GREATER_THAN(0, head.runs);
    WindowRecord rec;
    memcpy(&rec, speaker_windows_start + sizeof(head), sizeof(rec));
    const int8_t *x = reinterpret_cast<const int8_t *>(speaker_windows_start + sizeof(head) + sizeof(rec));
    const int8_t *y = x + rec.frames * rec.mels;

    const ai::Blob blob = ai::image_find(rec.entry, STORAGE_MODEL_KIND_ESPDL);
    TEST_ASSERT_NOT_NULL_MESSAGE(blob.data, rec.entry);
    static ai::EspdlNet net;
    const size_t psram_before = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    TEST_ASSERT_EQUAL(ESP_OK, net.build(blob, rec.entry));
    const size_t psram_bytes = psram_before - heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    const ai::Int8Tensor in = net.input();
    const ai::Int8Tensor out = net.output();
    TEST_ASSERT_EQUAL(rec.frames * rec.mels, in.elements);
    TEST_ASSERT_EQUAL(rec.dims, out.elements);
    TEST_ASSERT_EQUAL(rec.input_exponent, in.exponent);
    TEST_ASSERT_EQUAL(rec.output_exponent, out.exponent);

    int worst = 0;
    uint32_t off = 0;
    int64_t total_us = 0;
    int64_t peak_us = 0;
    for (uint32_t r = 0; r <= head.runs; r++) {
        memcpy(in.data, x, in.elements);
        const int64_t started_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, net.step());
        const int64_t took_us = esp_timer_get_time() - started_us;
        for (uint32_t k = 0; k < rec.dims; k++) {
            const int diff = abs(out.data[k] - y[k]);
            worst = diff > worst ? diff : worst;
            off += r == 0 && diff > kToleranceSteps;
        }
        // The first run is untimed: it pays the caches' first fill.
        if (r > 0) {
            total_us += took_us;
            peak_us = took_us > peak_us ? took_us : peak_us;
        }
        vTaskDelay(1);
    }
    printf("%s: %" PRIu32 " frames x %" PRIu32 " mels: worst |board - simulation| %d, %" PRId64
           " us mean, %" PRId64 " us peak a window over %" PRIu32 " runs, psram %u B with its tensors\n",
           rec.entry, rec.frames, rec.mels, worst, total_us / head.runs, peak_us, head.runs,
           (unsigned)psram_bytes);
    printf("%s: %" PRIu32 " of %" PRIu32 " values off the simulation; first board/simulation:", rec.entry,
           off, rec.dims);
    for (uint32_t k = 0; k < rec.dims && k < kShownValues; k++) {
        printf(" %d/%d", out.data[k], y[k]);
    }
    printf("\n");
    net.release();
    const esp_err_t tested = test_of(blob, true);
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                                           STORAGE_MODEL_LABEL_SLOT0);
    TEST_ASSERT_NOT_NULL(part);
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, kSectorBytes));
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, tested, rec.entry);
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, worst);
}

TEST_CASE("each cut of the speaker graph runs as its ESP-PPQ simulation does", "[ai_engine][speaker_cuts]")
{
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(0),
                              "models_0 lacks speaker_models.bin: run make ai-unit-speaker");
    int differing = 0;
    for (int i = 0; i < kCutsMax; i++) {
        char entry[STORAGE_MODEL_NAME_BYTES];
        snprintf(entry, sizeof(entry), "speaker_c%d", i);
        const ai::Blob blob = ai::image_find(entry, STORAGE_MODEL_KIND_ESPDL);
        if (blob.data == nullptr) { break; }
        const bool matches = test_of(blob, false) == ESP_OK;
        printf("%s: model->test() %s the simulation\n", entry, matches ? "matches" : "differs from");
        differing += !matches;
    }
    TEST_ASSERT_EQUAL(0, differing);
}

extern "C" void app_main(void)
{
    ESP_ERROR_CHECK(sys_storage_init());
    UNITY_BEGIN();
    // The whole-graph case erases model slot 0 when it ends, so the cuts run first.
    unity_run_tests_by_tag("[speaker_cuts]", false);
    unity_run_tests_by_tag("[speaker]", false);
    UNITY_END();
}
