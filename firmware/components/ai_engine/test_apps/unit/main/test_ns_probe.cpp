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
#include "storage_format.h"
#include "unity.h"

extern const uint8_t ns_streams_start[] asm("_binary_ns_streams_bin_start");

namespace {

constexpr size_t kSectorBytes = 4096;
constexpr int kToleranceSteps = 1; // one int8 step, as model->test() allows (KEHOACH 3.14)
constexpr size_t kNetsMax = 4;
constexpr uint32_t kStatesMax = 3;
constexpr size_t kStateBytesMax = 512;
constexpr size_t kNameBytes = 12;
constexpr int64_t kUsPerMs = 1000;

// Layout written by srpipe.tasks.ns.quant probe; each record's int8 data is padded to four bytes.
struct StreamsHead {
    char magic[4];
    uint32_t nets;
};
struct StreamRecord {
    char entry[8];
    uint32_t hops;
    uint32_t dims;
    uint32_t outputs;      // int8 values a hop
    uint32_t states;       // GRU states: input h<k>, output h<k>_next
    uint32_t state_values; // int8 values of every state a hop
    int32_t input_exponent;
    int32_t output_exponent;
};
static_assert(sizeof(StreamsHead) == 8 && sizeof(StreamRecord) == 36, "ns_streams.bin layout");

struct Ports {
    ai::Int8Tensor x;
    ai::Int8Tensor y;
    ai::Int8Tensor state[kStatesMax];
    ai::Int8Tensor next[kStatesMax];
};

Ports ports_of(ai::EspdlNet &net, const StreamRecord &rec)
{
    Ports p{net.input("x"), net.output("y"), {}, {}};
    for (uint32_t k = 0; k < rec.states; k++) {
        char name[kNameBytes];
        snprintf(name, sizeof(name), "h%" PRIu32, k);
        p.state[k] = net.input(name);
        snprintf(name, sizeof(name), "h%" PRIu32 "_next", k);
        p.next[k] = net.output(name);
    }
    return p;
}

struct Drift {
    int output;    // worst |board - simulation| of y
    int state;     // worst over every state after the hop
    int first_hop; // first hop with any difference, -1 if none
};

int worst_of(const int8_t *a, const int8_t *b, size_t n)
{
    int worst = 0;
    for (size_t i = 0; i < n; i++) {
        const int diff = abs(a[i] - b[i]);
        worst = diff > worst ? diff : worst;
    }
    return worst;
}

// Every hop from zero states against the simulation, each hop's states copied into the next when feed.
Drift stream(ai::EspdlNet &net, const Ports &p, const StreamRecord &rec, const int8_t *x, const int8_t *y,
             const int8_t *states, bool feed, int64_t *mean_us, int64_t *peak_us)
{
    for (uint32_t k = 0; k < rec.states; k++) {
        memset(p.state[k].data, 0, p.state[k].elements);
    }
    Drift drift{0, 0, -1};
    int64_t total_us = 0;
    *peak_us = 0;
    for (uint32_t t = 0; t < rec.hops; t++) {
        memcpy(p.x.data, x + t * rec.dims, rec.dims);
        const int64_t started_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, net.step());
        const int64_t took_us = esp_timer_get_time() - started_us;
        total_us += took_us;
        *peak_us = took_us > *peak_us ? took_us : *peak_us;
        const int output = worst_of(p.y.data, y + t * rec.outputs, rec.outputs);
        int state = 0;
        const int8_t *expected = states + t * rec.state_values;
        for (uint32_t k = 0; k < rec.states; k++) {
            const int diff = worst_of(p.next[k].data, expected, p.next[k].elements);
            state = diff > state ? diff : state;
            expected += p.next[k].elements;
        }
        drift.output = output > drift.output ? output : drift.output;
        drift.state = state > drift.state ? state : drift.state;
        if (drift.first_hop < 0 && (output > 0 || state > 0)) { drift.first_hop = static_cast<int>(t); }
        // esp-dl may reuse a state input it has read for a state output: stage them all first.
        static int8_t staged[kStateBytesMax];
        size_t at = 0;
        for (uint32_t k = 0; feed && k < rec.states; k++) {
            memcpy(staged + at, p.next[k].data, p.next[k].elements);
            at += p.next[k].elements;
        }
        at = 0;
        for (uint32_t k = 0; feed && k < rec.states; k++) {
            memcpy(p.state[k].data, staged + at, p.state[k].elements);
            at += p.state[k].elements;
        }
        // Let IDLE0 run between hops: seconds of hops on core 0 trip the task watchdog.
        vTaskDelay(1);
    }
    *mean_us = total_us / rec.hops;
    return drift;
}

void check_net(ai::EspdlNet &net, const StreamRecord &rec, const int8_t *x, const int8_t *y,
               const int8_t *states)
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
    TEST_ASSERT_LESS_OR_EQUAL(kStatesMax, rec.states);
    const Ports p = ports_of(net, rec);
    TEST_ASSERT_EQUAL(rec.dims, p.x.elements);
    TEST_ASSERT_EQUAL(rec.outputs, p.y.elements);
    TEST_ASSERT_EQUAL(rec.input_exponent, p.x.exponent);
    TEST_ASSERT_EQUAL(rec.output_exponent, p.y.exponent);
    size_t state_values = 0;
    for (uint32_t k = 0; k < rec.states; k++) {
        TEST_ASSERT_NOT_NULL(p.state[k].data);
        TEST_ASSERT_NOT_NULL(p.next[k].data);
        TEST_ASSERT_EQUAL(p.state[k].elements, p.next[k].elements);
        TEST_ASSERT_EQUAL(p.state[k].exponent, p.next[k].exponent);
        state_values += p.next[k].elements;
    }
    TEST_ASSERT_EQUAL(rec.state_values, state_values);
    TEST_ASSERT_LESS_OR_EQUAL(kStateBytesMax, state_values);

    int64_t mean_us = 0;
    int64_t peak_us = 0;
    const Drift fed = stream(net, p, rec, x, y, states, true, &mean_us, &peak_us);
    printf("%s streamed %" PRIu32 " hops with %" PRIu32
           " GRU states fed back: worst |board - simulation| %d on y, %d on the states, first off at hop %d; "
           "%" PRId64 " us mean, %" PRId64 " us peak a hop; built in %" PRId64 " ms, psram %u B\n",
           rec.entry, rec.hops, rec.states, fed.output, fed.state, fed.first_hop, mean_us, peak_us,
           build_us / kUsPerMs, (unsigned)psram_bytes);
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, fed.output);
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, fed.state);

    // Each hop from zero states instead: every output past the first must move away from the simulation.
    const Drift stateless = stream(net, p, rec, x, y, states, false, &mean_us, &peak_us);
    printf("%s without the states fed back: worst |board - simulation| %d on y\n", rec.entry,
           stateless.output);
    TEST_ASSERT_GREATER_THAN(kToleranceSteps, stateless.output);
}

} // namespace

TEST_CASE("every ns candidate streams with its GRU states fed back as its ESP-PPQ simulation does, timed",
          "[ns_probe]")
{
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                                           STORAGE_MODEL_LABEL_SLOT1);
    TEST_ASSERT_NOT_NULL(part);
    // The image outgrows the app partition, so make ai-unit writes it to model slot 1 through parttool.
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(1), "models_1 lacks ns_models.bin: run make ai-unit");

    StreamsHead head;
    memcpy(&head, ns_streams_start, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY("SRNS", head.magic, 4);
    TEST_ASSERT_LESS_OR_EQUAL(kNetsMax, head.nets);
    static ai::EspdlNet nets[kNetsMax];
    const uint8_t *at = ns_streams_start + sizeof(head);
    for (uint32_t n = 0; n < head.nets; n++) {
        StreamRecord rec;
        memcpy(&rec, at, sizeof(rec));
        const int8_t *x = reinterpret_cast<const int8_t *>(at + sizeof(rec));
        const int8_t *y = x + rec.hops * rec.dims;
        const int8_t *states = y + rec.hops * rec.outputs;
        check_net(nets[n], rec, x, y, states);
        nets[n].release();
        const size_t data = rec.hops * (rec.dims + rec.outputs + rec.state_values);
        at += sizeof(rec) + (data + 3) / 4 * 4;
    }

    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, kSectorBytes));
}
