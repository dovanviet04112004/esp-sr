#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ai_engine.h"
#include "core/espdl_net.hpp"
#include "core/model_image.hpp"
#include "dl_model_base.hpp"
#include "esp_heap_caps.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "storage_format.h"
#include "unity.h"

extern const uint8_t kws_models_start[] asm("_binary_kws_models_bin_start");
extern const uint8_t kws_models_end[] asm("_binary_kws_models_bin_end");
extern const uint8_t kws_windows_start[] asm("_binary_kws_windows_bin_start");

namespace {

constexpr size_t kSectorBytes = 4096;
constexpr int kToleranceSteps = 1; // one int8 step, as model->test() allows (KEHOACH 3.14)
constexpr size_t kSizesMax = 4;

// Layout written by srpipe.tasks.command.kws.quant probe; each record's int8 data is padded to four bytes.
struct WindowsHead {
    char magic[4];
    uint32_t sizes;
    uint32_t runs;
};
struct WindowRecord {
    char entry[8];
    uint32_t hops;
    uint32_t dims;
    uint32_t classes;
    int32_t input_exponent;
    int32_t output_exponent;
};
static_assert(sizeof(WindowsHead) == 12 && sizeof(WindowRecord) == 28, "kws_windows.bin layout");

void write_image(const esp_partition_t *part)
{
    const size_t bytes = kws_models_end - kws_models_start;
    const size_t erase = (bytes + kSectorBytes - 1) / kSectorBytes * kSectorBytes;
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, erase));
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_write(part, 0, kws_models_start, bytes));
}

void check_size(ai::EspdlNet &net, const WindowRecord &rec, const int8_t *x, const int8_t *y, uint32_t runs)
{
    const ai::Blob blob = ai::image_find(rec.entry, STORAGE_MODEL_KIND_ESPDL);
    TEST_ASSERT_NOT_NULL_MESSAGE(blob.data, rec.entry);
    {
        dl::Model model(reinterpret_cast<const char *>(blob.data), fbs::MODEL_LOCATION_IN_FLASH_RODATA, 0,
                        dl::MEMORY_MANAGER_GREEDY, nullptr, false);
        TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, model.test(), rec.entry);
    }
    const size_t psram_before = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    TEST_ASSERT_EQUAL(ESP_OK, net.build(blob, rec.entry));
    const size_t psram_bytes = psram_before - heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    const ai::Int8Tensor in = net.input();
    const ai::Int8Tensor out = net.output();
    TEST_ASSERT_EQUAL(rec.hops * rec.dims, in.elements);
    TEST_ASSERT_EQUAL(rec.classes, out.elements);
    TEST_ASSERT_EQUAL(rec.input_exponent, in.exponent);
    TEST_ASSERT_EQUAL(rec.output_exponent, out.exponent);
    memcpy(in.data, x, in.elements);
    TEST_ASSERT_EQUAL(ESP_OK, net.step());
    int worst = 0;
    for (uint32_t k = 0; k < rec.classes; k++) {
        const int diff = abs(out.data[k] - y[k]);
        worst = diff > worst ? diff : worst;
    }
    int64_t total_us = 0;
    int64_t peak_us = 0;
    for (uint32_t r = 0; r < runs; r++) {
        memcpy(in.data, x, in.elements);
        const int64_t started_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, net.step());
        const int64_t took_us = esp_timer_get_time() - started_us;
        total_us += took_us;
        peak_us = took_us > peak_us ? took_us : peak_us;
    }
    printf("%s: worst |board - simulation| %d, %" PRId64 " us mean, %" PRId64
           " us peak a window over %" PRIu32 " runs, psram %u B with its tensors\n",
           rec.entry, worst, total_us / runs, peak_us, runs, (unsigned)psram_bytes);
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, worst);
}

} // namespace

TEST_CASE("every kws DS-CNN size runs one window as its ESP-PPQ simulation does, timed", "[ai_engine]")
{
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                                           STORAGE_MODEL_LABEL_SLOT1);
    TEST_ASSERT_NOT_NULL(part);
    write_image(part);
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_load(1));

    WindowsHead head;
    memcpy(&head, kws_windows_start, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY("SRKW", head.magic, 4);
    TEST_ASSERT_LESS_OR_EQUAL(kSizesMax, head.sizes);
    static ai::EspdlNet nets[kSizesMax];
    const uint8_t *at = kws_windows_start + sizeof(head);
    for (uint32_t s = 0; s < head.sizes; s++) {
        WindowRecord rec;
        memcpy(&rec, at, sizeof(rec));
        const int8_t *x = reinterpret_cast<const int8_t *>(at + sizeof(rec));
        const int8_t *y = x + rec.hops * rec.dims;
        check_size(nets[s], rec, x, y, head.runs);
        nets[s].release();
        const size_t data = rec.hops * rec.dims + rec.classes;
        at += sizeof(rec) + (data + 3) / 4 * 4;
    }

    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, kSectorBytes));
}
