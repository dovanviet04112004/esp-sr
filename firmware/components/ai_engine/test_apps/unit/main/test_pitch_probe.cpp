#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ai_engine.h"
#include "core/espdl/espdl_ops.hpp"
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

extern const uint8_t pitch_window_start[] asm("_binary_pitch_window_bin_start");

namespace {

constexpr size_t kSectorBytes = 4096;
constexpr int kToleranceSteps = 0; // bit for bit, as model->test() asks of int8 (KEHOACH 3.14)
constexpr int kCutsMax = 16;
constexpr const char *kEntry = "pitch";
constexpr size_t kPortNameBytes = 32;
constexpr uint32_t kPortsMax = 4;
constexpr int64_t kUsPerMs = 1000;

// Layout written by srpipe.tasks.pitch.probe; each port's int8 values follow it, padded to four bytes.
struct ProbeHead {
    char magic[4];
    uint32_t runs;
    uint32_t frames;
    uint32_t ports; // the input, then each output
};
struct PortRecord {
    char name[kPortNameBytes]; // the graph's, NUL-ended
    uint32_t elements;
    int32_t exponent;
};
static_assert(sizeof(ProbeHead) == 16 && sizeof(PortRecord) == 40, "pitch_window.bin layout");

struct Port {
    PortRecord rec;
    const int8_t *values; // esp-dl's layout of the tensor
};

esp_err_t test_of(const ai::Blob &blob, bool profiled)
{
    ai::register_espdl_ops();
    dl::Model model(reinterpret_cast<const char *>(blob.data), fbs::MODEL_LOCATION_IN_FLASH_RODATA, 0,
                    dl::MEMORY_MANAGER_GREEDY, nullptr, false);
    if (profiled) {
        model.profile_module(true);
        // Back-to-back runs starve IDLE0 past the task watchdog, whose report lands inside a module's time.
        vTaskDelay(1);
    }
    return model.test();
}

uint32_t read_ports(const ProbeHead &head, Port *ports)
{
    const uint8_t *at = pitch_window_start + sizeof(head);
    for (uint32_t p = 0; p < head.ports; p++) {
        memcpy(&ports[p].rec, at, sizeof(PortRecord));
        TEST_ASSERT_NOT_NULL(memchr(ports[p].rec.name, '\0', kPortNameBytes));
        ports[p].values = reinterpret_cast<const int8_t *>(at + sizeof(PortRecord));
        at += sizeof(PortRecord) + (ports[p].rec.elements + 3) / 4 * 4;
    }
    return head.ports;
}

} // namespace

TEST_CASE("each cut of the pitch graph runs as its ESP-PPQ simulation does", "[ai_engine][pitch_cuts]")
{
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(0),
                              "models_0 lacks pitch_models.bin: run make ai-unit-pitch");
    int differing = 0;
    for (int i = 0; i < kCutsMax; i++) {
        char entry[STORAGE_MODEL_NAME_BYTES];
        snprintf(entry, sizeof(entry), "pitch_c%d", i);
        const ai::Blob blob = ai::image_find(entry, STORAGE_MODEL_KIND_ESPDL);
        if (blob.data == nullptr) { break; }
        const bool matches = test_of(blob, false) == ESP_OK;
        printf("%s: model->test() %s the simulation\n", entry, matches ? "matches" : "differs from");
        differing += !matches;
    }
    TEST_ASSERT_EQUAL(0, differing);
}

TEST_CASE("the pitch graph runs one window as its ESP-PPQ simulation does, timed", "[ai_engine][pitch]")
{
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(0),
                              "models_0 lacks pitch_models.bin: run make ai-unit-pitch");
    ProbeHead head;
    memcpy(&head, pitch_window_start, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY("SRPP", head.magic, 4);
    TEST_ASSERT_GREATER_THAN(0, head.runs);
    TEST_ASSERT_GREATER_THAN(0, head.frames);
    TEST_ASSERT_TRUE(head.ports >= 2 && head.ports <= kPortsMax);
    Port ports[kPortsMax];
    const uint32_t count = read_ports(head, ports);

    const ai::Blob blob = ai::image_find(kEntry, STORAGE_MODEL_KIND_ESPDL);
    TEST_ASSERT_NOT_NULL_MESSAGE(blob.data, kEntry);
    static ai::EspdlNet net;
    const size_t psram_before = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    const int64_t build_started_us = esp_timer_get_time();
    TEST_ASSERT_EQUAL(ESP_OK, net.build(blob, kEntry));
    const int64_t build_us = esp_timer_get_time() - build_started_us;
    const size_t psram_bytes = psram_before - heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    const ai::Int8Tensor in = net.input(ports[0].rec.name);
    TEST_ASSERT_NOT_NULL_MESSAGE(in.data, ports[0].rec.name);
    TEST_ASSERT_EQUAL(ports[0].rec.elements, in.elements);
    TEST_ASSERT_EQUAL(ports[0].rec.exponent, in.exponent);
    ai::Int8Tensor outs[kPortsMax] = {};
    for (uint32_t p = 1; p < count; p++) {
        outs[p] = net.output(ports[p].rec.name);
        TEST_ASSERT_NOT_NULL_MESSAGE(outs[p].data, ports[p].rec.name);
        TEST_ASSERT_EQUAL(ports[p].rec.elements, outs[p].elements);
        TEST_ASSERT_EQUAL(ports[p].rec.exponent, outs[p].exponent);
    }

    int worst = 0;
    uint32_t off = 0;
    int64_t total_us = 0;
    int64_t peak_us = 0;
    for (uint32_t r = 0; r <= head.runs; r++) {
        memcpy(in.data, ports[0].values, in.elements);
        const int64_t started_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, net.step());
        const int64_t took_us = esp_timer_get_time() - started_us;
        for (uint32_t p = 1; p < count; p++) {
            for (uint32_t k = 0; k < outs[p].elements; k++) {
                const int diff = abs(outs[p].data[k] - ports[p].values[k]);
                worst = diff > worst ? diff : worst;
                off += r == 0 && diff > kToleranceSteps;
            }
        }
        // The first run is untimed: it pays the caches' first fill.
        if (r > 0) {
            total_us += took_us;
            peak_us = took_us > peak_us ? took_us : peak_us;
        }
        vTaskDelay(1);
    }
    const int64_t mean_us = total_us / head.runs;
    printf("%s: %" PRIu32 " frames: worst |board - simulation| %d, %" PRIu32 " values off; %" PRId64
           " us mean, %" PRId64 " us peak a window over %" PRIu32 " runs, %" PRId64
           " us a hop; built in %" PRId64 " ms, psram %u B with its tensors\n",
           kEntry, head.frames, worst, off, mean_us, peak_us, head.runs, mean_us / head.frames,
           build_us / kUsPerMs, (unsigned)psram_bytes);
    net.release();
    const esp_err_t tested = test_of(blob, true);
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                                           STORAGE_MODEL_LABEL_SLOT0);
    TEST_ASSERT_NOT_NULL(part);
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, kSectorBytes));
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, tested, kEntry);
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, worst);
}

extern "C" void app_main(void)
{
    ESP_ERROR_CHECK(sys_storage_init());
    UNITY_BEGIN();
    // The whole-graph case erases model slot 0 when it ends, so the cuts run first.
    unity_run_tests_by_tag("[pitch_cuts]", false);
    unity_run_tests_by_tag("[pitch]", false);
    UNITY_END();
}
