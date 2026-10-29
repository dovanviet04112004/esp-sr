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
extern const uint8_t wake_models_start[] asm("_binary_wake_models_bin_start");
extern const uint8_t wake_models_end[] asm("_binary_wake_models_bin_end");
extern const uint8_t wake_branch_start[] asm("_binary_wake_branch_bin_start");

namespace {

constexpr size_t kSectorBytes = 4096;
constexpr int kToleranceSteps = 1; // one int8 step, as model->test() allows (KEHOACH 3.14)

// Layout written by srpipe.tasks.wake.quant probe: int8 input hops x bands, then int8 output hops x outputs.
struct StreamHead {
    char magic[4];
    uint32_t hops;
    uint32_t bands;
    uint32_t outputs;
    uint32_t step; // hops a call
    int32_t input_exponent;
    int32_t output_exponent;
};
static_assert(sizeof(StreamHead) == 28, "stream.bin head is 28 bytes");

// Layout written by srpipe.tasks.wake.quant branch_probe: float32 raw log-mel hops x bands, uint16 scores.
struct BranchHead {
    char magic[4];
    uint32_t hops;
    uint32_t bands;
    uint32_t step;   // hops a call of the image's graph
    uint32_t smooth; // hops the scores average over
};
static_assert(sizeof(BranchHead) == 20, "wake_branch.bin head is 20 bytes");

void write_image(const esp_partition_t *part, const uint8_t *start, const uint8_t *end)
{
    const size_t bytes = end - start;
    const size_t erase = (bytes + kSectorBytes - 1) / kSectorBytes * kSectorBytes;
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, erase));
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_write(part, 0, start, bytes));
}

// Calls whose returned score differs from the frame delay calls back; mean and peak us of a call that runs
// the net.
uint32_t branch_off(const BranchHead &head, const float *raw, const uint16_t *want, uint32_t frames,
                    int64_t *mean_us, int64_t *peak_us)
{
    const size_t delay = ai_engine_wake_delay();
    uint32_t off = 0;
    int64_t total_us = 0;
    *peak_us = 0;
    for (uint32_t f = 0; f < frames; f++) {
        uint16_t score = UINT16_MAX;
        const int64_t started_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, ai_engine_wake_step(raw + (size_t)f * head.bands, &score));
        const int64_t took_us = esp_timer_get_time() - started_us;
        if ((f + 1) % head.step == 0) {
            total_us += took_us;
            *peak_us = took_us > *peak_us ? took_us : *peak_us;
        }
        off += score != (f >= delay ? want[f - delay] : 0) ? 1 : 0;
    }
    *mean_us = total_us / (frames / head.step);
    return off;
}

// Worst |board - simulation| over all hops fed step a call from the caches as they stand; us per call.
int stream(ai::EspdlNet &net, const StreamHead &head, const int8_t *x, const int8_t *y, int64_t *mean_us,
           int64_t *peak_us)
{
    const ai::Int8Tensor in = net.input();
    const ai::Int8Tensor out = net.output();
    int worst = 0;
    int64_t total_us = 0;
    *peak_us = 0;
    for (uint32_t t = 0; t < head.hops; t += head.step) {
        memcpy(in.data, x + t * head.bands, head.step * head.bands);
        const int64_t started_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, net.step());
        const int64_t took_us = esp_timer_get_time() - started_us;
        total_us += took_us;
        *peak_us = took_us > *peak_us ? took_us : *peak_us;
        for (uint32_t k = 0; k < head.step * head.outputs; k++) {
            const int diff = abs(out.data[k] - y[t * head.outputs + k]);
            worst = diff > worst ? diff : worst;
        }
    }
    *mean_us = total_us / (head.hops / head.step);
    return worst;
}

} // namespace

TEST_CASE("an ESP-PPQ network streamed hop by hop matches its whole-sequence simulation", "[ai_engine]")
{
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                                           STORAGE_MODEL_LABEL_SLOT1);
    TEST_ASSERT_NOT_NULL(part);
    write_image(part, probe_models_start, probe_models_end);
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_load(1));
    const ai::Blob blob = ai::image_find("probe", STORAGE_MODEL_KIND_ESPDL);
    TEST_ASSERT_NOT_NULL(blob.data);

    {
        dl::Model model(reinterpret_cast<const char *>(blob.data), fbs::MODEL_LOCATION_IN_FLASH_RODATA, 0,
                        dl::MEMORY_MANAGER_GREEDY, nullptr, false);
        TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, model.test(), "the first call exported for model->test()");
        model.profile_module(true);
    }

    StreamHead head;
    memcpy(&head, probe_stream_start, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY("SRST", head.magic, 4);
    TEST_ASSERT_TRUE(head.step > 0 && head.hops % head.step == 0);
    const int8_t *x = reinterpret_cast<const int8_t *>(probe_stream_start + sizeof(head));
    const int8_t *y = x + head.hops * head.bands;

    static ai::EspdlNet net;
    TEST_ASSERT_EQUAL(ESP_OK, net.build(blob, "probe"));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, net.build(blob, "probe"));
    TEST_ASSERT_EQUAL(head.step * head.bands, net.input().elements);
    TEST_ASSERT_EQUAL(head.step * head.outputs, net.output().elements);
    TEST_ASSERT_EQUAL(head.input_exponent, net.input().exponent);
    TEST_ASSERT_EQUAL(head.output_exponent, net.output().exponent);

    int64_t mean_us = 0;
    int64_t peak_us = 0;
    const int worst = stream(net, head, x, y, &mean_us, &peak_us);
    printf("probe streamed %" PRIu32 " hops, %" PRIu32 " a call: worst |board - simulation| %d, %" PRId64
           " us mean, %" PRId64 " us peak a call, %" PRId64 " us a hop\n",
           head.hops, head.step, worst, mean_us, peak_us, mean_us / (int64_t)head.step);
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, worst);

    // Without reset the caches still hold the last hops, so the same input must come out different.
    const int stale = stream(net, head, x, y, &mean_us, &peak_us);
    printf("same input again without reset: worst |board - simulation| %d\n", stale);
    TEST_ASSERT_GREATER_THAN(kToleranceSteps, stale);
    net.reset();
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, stream(net, head, x, y, &mean_us, &peak_us));
    net.release();

    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, kSectorBytes));
}

TEST_CASE("ai_engine's wake branch scores raw log-mel frames as chip_scores does, timed", "[ai_engine]")
{
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                                           STORAGE_MODEL_LABEL_SLOT1);
    TEST_ASSERT_NOT_NULL(part);
    write_image(part, wake_models_start, wake_models_end);
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_load(1));
    TEST_ASSERT_TRUE(ai_engine_has(AI_ENGINE_MODEL_WAKE));

    BranchHead head;
    memcpy(&head, wake_branch_start, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY("SRWB", head.magic, 4);
    TEST_ASSERT_EQUAL(head.step - 1, ai_engine_wake_delay());
    const float *raw = reinterpret_cast<const float *>(wake_branch_start + sizeof(head));
    const uint16_t *want = reinterpret_cast<const uint16_t *>(raw + (size_t)head.hops * head.bands);

    int64_t mean_us = 0;
    int64_t peak_us = 0;
    const uint32_t off = branch_off(head, raw, want, head.hops, &mean_us, &peak_us);
    printf("wake branch: %" PRIu32 " frames, %" PRIu32 " a call, scored %u behind over %" PRIu32
           " hops: %" PRIu32 " scores off; %" PRId64 " us mean, %" PRId64
           " us peak a call that runs the net\n",
           head.hops, head.step, (unsigned)ai_engine_wake_delay(), head.smooth, off, mean_us, peak_us);
    TEST_ASSERT_EQUAL(0, off);

    // Without reset the scores carry the last frames, so the same frames must come out otherwise.
    const uint32_t stale = branch_off(head, raw, want, head.hops, &mean_us, &peak_us);
    printf("same frames again without reset: %" PRIu32 " scores off\n", stale);
    TEST_ASSERT_GREATER_THAN(0, stale);
    ai_engine_wake_reset();
    TEST_ASSERT_EQUAL(0, branch_off(head, raw, want, head.hops, &mean_us, &peak_us));

    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, kSectorBytes));
}
