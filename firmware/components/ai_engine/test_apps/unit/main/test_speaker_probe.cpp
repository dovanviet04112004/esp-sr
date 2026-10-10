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

// Layout written by srpipe.tasks.speaker.probe; the record's int8 data is padded to four bytes.
struct WindowsHead {
    char magic[4];
    uint32_t runs;
};
struct WindowRecord {
    char entry[STORAGE_MODEL_NAME_BYTES];
    uint32_t mels;
    uint32_t frames;
    uint32_t dims;
    int32_t input_exponent;
    int32_t output_exponent;
};
static_assert(sizeof(WindowsHead) == 8 && sizeof(WindowRecord) == 36, "speaker_windows.bin layout");

void run_test_of(const char *entry, const ai::Blob &blob)
{
    dl::Model model(reinterpret_cast<const char *>(blob.data), fbs::MODEL_LOCATION_IN_FLASH_RODATA, 0,
                    dl::MEMORY_MANAGER_GREEDY, nullptr, false);
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, model.test(), entry);
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
    const int8_t *y = x + rec.mels * rec.frames;

    const ai::Blob blob = ai::image_find(rec.entry, STORAGE_MODEL_KIND_ESPDL);
    TEST_ASSERT_NOT_NULL_MESSAGE(blob.data, rec.entry);
    run_test_of(rec.entry, blob);
    static ai::EspdlNet net;
    const size_t psram_before = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    TEST_ASSERT_EQUAL(ESP_OK, net.build(blob, rec.entry));
    const size_t psram_bytes = psram_before - heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    const ai::Int8Tensor in = net.input();
    const ai::Int8Tensor out = net.output();
    TEST_ASSERT_EQUAL(rec.mels * rec.frames, in.elements);
    TEST_ASSERT_EQUAL(rec.dims, out.elements);
    TEST_ASSERT_EQUAL(rec.input_exponent, in.exponent);
    TEST_ASSERT_EQUAL(rec.output_exponent, out.exponent);

    int worst = 0;
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
        }
        // The first run is untimed: it pays the caches' first fill.
        if (r > 0) {
            total_us += took_us;
            peak_us = took_us > peak_us ? took_us : peak_us;
        }
        vTaskDelay(1);
    }
    printf("%s: %" PRIu32 " mels x %" PRIu32 " frames: worst |board - simulation| %d, %" PRId64
           " us mean, %" PRId64 " us peak a window over %" PRIu32 " runs, psram %u B with its tensors\n",
           rec.entry, rec.mels, rec.frames, worst, total_us / head.runs, peak_us, head.runs,
           (unsigned)psram_bytes);
    net.release();
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                                           STORAGE_MODEL_LABEL_SLOT0);
    TEST_ASSERT_NOT_NULL(part);
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, kSectorBytes));
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, worst);
}

extern "C" void app_main(void)
{
    ESP_ERROR_CHECK(sys_storage_init());
    UNITY_BEGIN();
    unity_run_tests_by_tag("[speaker]", false);
    UNITY_END();
}
