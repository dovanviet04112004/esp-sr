#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ai_engine.h"
#include "core/espdl_net.hpp"
#include "core/model_image.hpp"
#include "dl_model_base.hpp"
#include "esp_partition.h"
#include "esp_timer.h"
#include "storage_format.h"
#include "unity.h"

extern const uint8_t probe_models_start[] asm("_binary_models_bin_start");
extern const uint8_t probe_models_end[] asm("_binary_models_bin_end");
extern const uint8_t probe_stream_start[] asm("_binary_stream_bin_start");

namespace {

constexpr size_t kSectorBytes = 4096;
constexpr int kToleranceSteps = 1; // one int8 step, as model->test() allows (KEHOACH 3.14)

// Layout written by srpipe.tasks.wake.quant probe: int8 input hops x bands, then int8 output hops x outputs.
struct StreamHead {
    char magic[4];
    uint32_t hops;
    uint32_t bands;
    uint32_t outputs;
    int32_t input_exponent;
    int32_t output_exponent;
};
static_assert(sizeof(StreamHead) == 24, "stream.bin head is 24 bytes");

void write_probe_image(const esp_partition_t *part)
{
    const size_t bytes = probe_models_end - probe_models_start;
    const size_t erase = (bytes + kSectorBytes - 1) / kSectorBytes * kSectorBytes;
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, erase));
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_write(part, 0, probe_models_start, bytes));
}

// Worst |board - simulation| over hops run from the caches as they stand; mean and peak us per step.
int stream(ai::EspdlNet &net, const StreamHead &head, const int8_t *x, const int8_t *y, uint32_t hops,
           int64_t *mean_us, int64_t *peak_us)
{
    const ai::Int8Tensor in = net.input();
    const ai::Int8Tensor out = net.output();
    int worst = 0;
    int64_t total_us = 0;
    *peak_us = 0;
    for (uint32_t t = 0; t < hops; t++) {
        memcpy(in.data, x + t * head.bands, head.bands);
        const int64_t started_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, net.step());
        const int64_t took_us = esp_timer_get_time() - started_us;
        total_us += took_us;
        *peak_us = took_us > *peak_us ? took_us : *peak_us;
        for (uint32_t k = 0; k < head.outputs; k++) {
            const int diff = abs(out.data[k] - y[t * head.outputs + k]);
            worst = diff > worst ? diff : worst;
        }
    }
    *mean_us = total_us / hops;
    return worst;
}

} // namespace

TEST_CASE("an ESP-PPQ network streamed hop by hop matches its whole-sequence simulation", "[ai_engine]")
{
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                                           STORAGE_MODEL_LABEL_SLOT1);
    TEST_ASSERT_NOT_NULL(part);
    write_probe_image(part);
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_load(1));
    const ai::Blob blob = ai::image_find("probe", STORAGE_MODEL_KIND_ESPDL);
    TEST_ASSERT_NOT_NULL(blob.data);

    {
        dl::Model model(reinterpret_cast<const char *>(blob.data), fbs::MODEL_LOCATION_IN_FLASH_RODATA, 0,
                        dl::MEMORY_MANAGER_GREEDY, nullptr, false);
        TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, model.test(), "the first hop exported for model->test()");
    }

    StreamHead head;
    memcpy(&head, probe_stream_start, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY("SRST", head.magic, 4);
    const int8_t *x = reinterpret_cast<const int8_t *>(probe_stream_start + sizeof(head));
    const int8_t *y = x + head.hops * head.bands;

    static ai::EspdlNet net;
    TEST_ASSERT_EQUAL(ESP_OK, net.build(blob, "probe"));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, net.build(blob, "probe"));
    TEST_ASSERT_EQUAL(head.bands, net.input().elements);
    TEST_ASSERT_EQUAL(head.outputs, net.output().elements);
    TEST_ASSERT_EQUAL(head.input_exponent, net.input().exponent);
    TEST_ASSERT_EQUAL(head.output_exponent, net.output().exponent);

    int64_t mean_us = 0;
    int64_t peak_us = 0;
    const int worst = stream(net, head, x, y, head.hops, &mean_us, &peak_us);
    printf("probe streamed %" PRIu32 " hops: worst |board - simulation| %d, %" PRId64 " us mean, %" PRId64
           " us peak per hop\n",
           head.hops, worst, mean_us, peak_us);
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, worst);

    // Without reset the caches still hold the last hops, so the same input must come out different.
    const int stale = stream(net, head, x, y, head.hops, &mean_us, &peak_us);
    printf("same input again without reset: worst |board - simulation| %d\n", stale);
    TEST_ASSERT_GREATER_THAN(kToleranceSteps, stale);
    net.reset();
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, stream(net, head, x, y, head.hops, &mean_us, &peak_us));

    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, kSectorBytes));
}
