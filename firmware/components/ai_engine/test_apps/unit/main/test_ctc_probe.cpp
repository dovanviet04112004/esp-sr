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
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gen_grid.h"
#include "storage_format.h"
#include "unity.h"

extern const uint8_t ctc_streams_start[] asm("_binary_ctc_streams_bin_start");

namespace {

constexpr size_t kSectorBytes = 4096;
constexpr int kToleranceSteps = 1; // one int8 step, as model->test() allows (KEHOACH 3.14)
constexpr size_t kNetsMax = 2;
constexpr int64_t kUsPerMs = 1000;

// Layout written by srpipe.tasks.command.ctc.probe; each record's int8 data is padded to four bytes.
struct StreamsHead {
    char magic[4];
    uint32_t nets;
};
struct StreamRecord {
    char entry[8];
    uint32_t step_hops;
    uint32_t dims;
    uint32_t outputs; // int8 values a step: frames a step times channels
    uint32_t steps;
    int32_t input_exponent;
    int32_t output_exponent;
};
static_assert(sizeof(StreamsHead) == 8 && sizeof(StreamRecord) == 32, "ctc_streams.bin layout");

// Worst |board - simulation| over every step from the caches as they stand; mean and peak us a step.
int stream(ai::EspdlNet &net, const StreamRecord &rec, const int8_t *x, const int8_t *y, int64_t *mean_us,
           int64_t *peak_us)
{
    const ai::Int8Tensor in = net.input();
    const ai::Int8Tensor out = net.output();
    const size_t step_inputs = rec.step_hops * rec.dims;
    int worst = 0;
    int64_t total_us = 0;
    *peak_us = 0;
    for (uint32_t s = 0; s < rec.steps; s++) {
        memcpy(in.data, x + s * step_inputs, step_inputs);
        const int64_t started_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, net.step());
        const int64_t took_us = esp_timer_get_time() - started_us;
        total_us += took_us;
        *peak_us = took_us > *peak_us ? took_us : *peak_us;
        for (uint32_t k = 0; k < rec.outputs; k++) {
            const int diff = abs(out.data[k] - y[s * rec.outputs + k]);
            worst = diff > worst ? diff : worst;
        }
        // Let IDLE0 run between steps: seconds of steps on core 0 trip the task watchdog.
        vTaskDelay(1);
    }
    *mean_us = total_us / rec.steps;
    return worst;
}

void check_net(ai::EspdlNet &net, const StreamRecord &rec, const int8_t *x, const int8_t *y)
{
    const ai::Blob blob = ai::image_find(rec.entry, STORAGE_MODEL_KIND_ESPDL);
    TEST_ASSERT_NOT_NULL_MESSAGE(blob.data, rec.entry);
    {
        dl::Model model(reinterpret_cast<const char *>(blob.data), fbs::MODEL_LOCATION_IN_FLASH_RODATA, 0,
                        dl::MEMORY_MANAGER_GREEDY, nullptr, false);
        TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, model.test(), rec.entry);
    }
    const size_t psram_before = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    const int64_t build_started_us = esp_timer_get_time();
    TEST_ASSERT_EQUAL(ESP_OK, net.build(blob, rec.entry));
    const int64_t build_us = esp_timer_get_time() - build_started_us;
    const size_t psram_bytes = psram_before - heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    TEST_ASSERT_EQUAL(rec.step_hops * rec.dims, net.input().elements);
    TEST_ASSERT_EQUAL(rec.outputs, net.output().elements);
    TEST_ASSERT_EQUAL(rec.input_exponent, net.input().exponent);
    TEST_ASSERT_EQUAL(rec.output_exponent, net.output().exponent);

    int64_t mean_us = 0;
    int64_t peak_us = 0;
    const int worst = stream(net, rec, x, y, &mean_us, &peak_us);
    const int64_t step_audio_ms = rec.step_hops * GEN_GRID_HOP_SAMPLES * kUsPerMs / GEN_GRID_SAMPLE_RATE_HZ;
    printf("%s streamed %" PRIu32 " steps of %" PRIu32 " hops (%" PRId64
           " ms of audio): worst |board - simulation| "
           "%d, %" PRId64 " us mean, %" PRId64 " us peak a step; built in %" PRId64 " ms, psram %u B\n",
           rec.entry, rec.steps, rec.step_hops, step_audio_ms, worst, mean_us, peak_us, build_us / kUsPerMs,
           (unsigned)psram_bytes);
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, worst);

    // Without reset the caches still hold the last steps, so the same input must come out different.
    const int stale = stream(net, rec, x, y, &mean_us, &peak_us);
    printf("%s again without reset: worst |board - simulation| %d\n", rec.entry, stale);
    TEST_ASSERT_GREATER_THAN(kToleranceSteps, stale);
    net.reset();
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, stream(net, rec, x, y, &mean_us, &peak_us));
}

} // namespace

TEST_CASE("the ctc net and one of its layers stream as their ESP-PPQ simulation does, timed", "[ai_engine]")
{
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                                           STORAGE_MODEL_LABEL_SLOT0);
    TEST_ASSERT_NOT_NULL(part);
    // The image outgrows the app partition, so make ai-unit writes it to model slot 0 through parttool.
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(0), "models_0 lacks ctc_models.bin: run make ai-unit");

    StreamsHead head;
    memcpy(&head, ctc_streams_start, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY("SRCT", head.magic, 4);
    TEST_ASSERT_LESS_OR_EQUAL(kNetsMax, head.nets);
    static ai::EspdlNet nets[kNetsMax];
    const uint8_t *at = ctc_streams_start + sizeof(head);
    for (uint32_t n = 0; n < head.nets; n++) {
        StreamRecord rec;
        memcpy(&rec, at, sizeof(rec));
        const int8_t *x = reinterpret_cast<const int8_t *>(at + sizeof(rec));
        const int8_t *y = x + rec.steps * rec.step_hops * rec.dims;
        check_net(nets[n], rec, x, y);
        const size_t data = rec.steps * (rec.step_hops * rec.dims + rec.outputs);
        at += sizeof(rec) + (data + 3) / 4 * 4;
    }

    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, kSectorBytes));
}
