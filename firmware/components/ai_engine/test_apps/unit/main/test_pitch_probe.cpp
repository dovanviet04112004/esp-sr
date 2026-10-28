#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ai_engine.h"
#include "core/espdl/espdl_ops.hpp"
#include "core/espdl/runtime/espdl_loan.hpp"
#include "core/espdl_net.hpp"
#include "core/model_image.hpp"
#include "dl_model_base.hpp"
#include "dsp_spec/swift_front.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gen_grid.h"
#include "storage_format.h"
#include "sys_storage.h"
#include "unity.h"

extern const uint8_t pitch_window_start[] asm("_binary_pitch_window_bin_start");

namespace {

constexpr size_t kSectorBytes = 4096;
constexpr int kToleranceSteps = 0; // bit for bit, as model->test() asks of int8 (KEHOACH 3.14)
constexpr int kCutsMax = 16;
constexpr const char *kEntry = "pitch";
constexpr const char *kStreamMagic = "SRPS";
constexpr const char *kChainMagic = "SRPC";
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
// The streaming record's port: PortRecord, then the frames the output trails the input.
struct StreamPortRecord {
    char name[kPortNameBytes];
    uint32_t elements; // the whole window's
    int32_t exponent;
    int32_t delay;
};
static_assert(sizeof(ProbeHead) == 16 && sizeof(PortRecord) == 40 && sizeof(StreamPortRecord) == 44,
              "pitch_window.bin layout");

// The chain record: then the hops' int16 samples, then the checked frames' float32 ln Hz and confidence.
struct ChainHead {
    char magic[4];
    uint32_t runs;
    uint32_t hops;
    uint32_t checked; // decoded frames held to the mirror
    uint32_t delay;   // frames they trail the front's
};
static_assert(sizeof(ChainHead) == 20, "pitch_window.bin chain layout");

struct Port {
    PortRecord rec;
    const int8_t *values; // esp-dl's layout of the tensor
};

struct StreamPort {
    StreamPortRecord rec;
    const int8_t *values; // esp-dl's layout, frames along the whole window
};

esp_err_t test_of(const ai::Blob &blob, bool profiled)
{
    ai::register_espdl_ops();
    dl::Model model(reinterpret_cast<const char *>(blob.data), fbs::MODEL_LOCATION_IN_FLASH_RODATA, 0,
                    dl::MEMORY_MANAGER_GREEDY, nullptr, false);
    // One run builds every module, so the rest run on the ops' loan as EspdlNet::step does.
    (void)ai::loan_take_asked();
    model.run();
    // Back-to-back runs starve IDLE0 past the task watchdog, whose report lands inside a module's time.
    vTaskDelay(1);
    const ai::WeightLoan loan(ai::loan_take_asked());
    if (profiled) {
        model.profile_module(true);
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

uint32_t read_stream_ports(const ProbeHead &head, StreamPort *ports)
{
    const uint8_t *at = pitch_window_start + sizeof(head);
    for (uint32_t p = 0; p < head.ports; p++) {
        memcpy(&ports[p].rec, at, sizeof(StreamPortRecord));
        TEST_ASSERT_NOT_NULL(memchr(ports[p].rec.name, '\0', kPortNameBytes));
        ports[p].values = reinterpret_cast<const int8_t *>(at + sizeof(StreamPortRecord));
        at += sizeof(StreamPortRecord) + (ports[p].rec.elements + 3) / 4 * 4;
    }
    return head.ports;
}

// The window's frames call..call + step into in, [1, bands, step, lanes] of the window's [1, bands, frames,
// lanes].
void feed(const ai::Int8Tensor &in, const int8_t *window, uint32_t frames, uint32_t call)
{
    const size_t bands = in.dims[1];
    const size_t step_bytes = in.dims[2] * in.dims[3];
    for (size_t b = 0; b < bands; b++) {
        memcpy(in.data + b * step_bytes, window + (b * frames + call) * in.dims[3], step_bytes);
    }
}

// Values of out off the window's, delay frames behind the call; first_off takes the earliest frame off.
uint32_t off_in(const ai::Int8Tensor &out, const StreamPort &port, uint32_t frames, uint32_t call, int *worst,
                int64_t *first_off)
{
    const size_t step = out.dims[out.rank - 1];
    const size_t rows = out.elements / step;
    uint32_t off = 0;
    for (size_t j = 0; j < step; j++) {
        const int64_t t = static_cast<int64_t>(call + j) - port.rec.delay;
        if (t < 0) { continue; }
        for (size_t r = 0; r < rows; r++) {
            const int diff = abs(out.data[r * step + j] - port.values[r * frames + t]);
            *worst = diff > *worst ? diff : *worst;
            if (diff > kToleranceSteps) {
                off++;
                *first_off = *first_off < 0 || t < *first_off ? t : *first_off;
            }
        }
    }
    return off;
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

TEST_CASE("the streaming pitch graph runs a window a few frames a call as its simulation does, timed",
          "[ai_engine][pitch_stream]")
{
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(0),
                              "models_0 lacks pitch_models.bin: run make ai-unit-pitch STEP=<frames>");
    ProbeHead head;
    memcpy(&head, pitch_window_start, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY(kStreamMagic, head.magic, 4);
    TEST_ASSERT_GREATER_THAN(0, head.runs);
    TEST_ASSERT_TRUE(head.ports >= 2 && head.ports <= kPortsMax);
    StreamPort ports[kPortsMax];
    const uint32_t count = read_stream_ports(head, ports);

    const ai::Blob blob = ai::image_find(kEntry, STORAGE_MODEL_KIND_ESPDL);
    TEST_ASSERT_NOT_NULL_MESSAGE(blob.data, kEntry);
    static ai::EspdlNet net;
    const size_t psram_before = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    TEST_ASSERT_EQUAL(ESP_OK, net.build(blob, kEntry));
    const size_t psram_bytes = psram_before - heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    const ai::Int8Tensor in = net.input(ports[0].rec.name);
    TEST_ASSERT_NOT_NULL_MESSAGE(in.data, ports[0].rec.name);
    TEST_ASSERT_EQUAL(4, in.rank);
    TEST_ASSERT_EQUAL(ports[0].rec.exponent, in.exponent);
    const uint32_t step = in.dims[2];
    TEST_ASSERT_EQUAL(ports[0].rec.elements, in.elements / step * head.frames);
    TEST_ASSERT_EQUAL(0, head.frames % step);
    ai::Int8Tensor outs[kPortsMax] = {};
    for (uint32_t p = 1; p < count; p++) {
        outs[p] = net.output(ports[p].rec.name);
        TEST_ASSERT_NOT_NULL_MESSAGE(outs[p].data, ports[p].rec.name);
        TEST_ASSERT_EQUAL(ports[p].rec.exponent, outs[p].exponent);
        TEST_ASSERT_EQUAL(ports[p].rec.elements, outs[p].elements / step * head.frames);
    }

    int worst = 0;
    uint32_t off = 0;
    uint32_t port_off[kPortsMax] = {};
    int64_t first_off[kPortsMax] = {-1, -1, -1, -1};
    int64_t total_us = 0;
    int64_t peak_us = 0;
    for (uint32_t r = 0; r <= head.runs; r++) {
        net.reset();
        for (uint32_t call = 0; call < head.frames; call += step) {
            feed(in, ports[0].values, head.frames, call);
            const int64_t started_us = esp_timer_get_time();
            TEST_ASSERT_EQUAL(ESP_OK, net.step());
            const int64_t took_us = esp_timer_get_time() - started_us;
            for (uint32_t p = 1; p < count && r == 0; p++) {
                const uint32_t found = off_in(outs[p], ports[p], head.frames, call, &worst, &first_off[p]);
                port_off[p] += found;
                off += found;
            }
            // The first pass is untimed: it pays the caches' first fill.
            if (r > 0) {
                total_us += took_us;
                peak_us = took_us > peak_us ? took_us : peak_us;
            }
        }
        vTaskDelay(1);
    }
    const int64_t calls = static_cast<int64_t>(head.runs) * (head.frames / step);
    const int64_t mean_us = total_us / calls;
    printf("%s: %" PRIu32 " frames, %" PRIu32 " a call: worst |board - simulation| %d, %" PRIu32
           " values off; %" PRId64 " us mean, %" PRId64 " us peak a call over %" PRId64 " calls, %" PRId64
           " us a hop; psram %u B with its tensors\n",
           kEntry, head.frames, step, worst, off, mean_us, peak_us, calls, mean_us / step,
           (unsigned)psram_bytes);
    for (uint32_t p = 1; p < count; p++) {
        printf("%s: %s, %" PRId32 " frames behind: %" PRIu32 " values off, the earliest at frame %" PRId64
               "\n",
               kEntry, ports[p].rec.name, ports[p].rec.delay, port_off[p], first_off[p]);
    }
    net.release();
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                                           STORAGE_MODEL_LABEL_SLOT0);
    TEST_ASSERT_NOT_NULL(part);
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, kSectorBytes));
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, worst);
}

// Frames whose bits differ from want's, at frames delay behind the calls' first front frame, up to checked.
uint32_t chain_off(const float *got, const float *want, size_t step, int64_t first, uint32_t checked,
                   uint32_t *seen)
{
    uint32_t off = 0;
    for (size_t j = 0; j < step; j++) {
        const int64_t t = first + static_cast<int64_t>(j);
        if (t < 0 || t >= checked) { continue; }
        off += memcmp(&got[j], &want[t], sizeof(float)) != 0;
        (*seen)++;
    }
    return off;
}

TEST_CASE("PCM through SwiftF0's front end, streaming network and decode as the mirror chain, timed",
          "[ai_engine][pitch_chain]")
{
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(0),
                              "models_0 lacks pitch_models.bin: run make ai-unit-pitch CHAIN=1");
    TEST_ASSERT_TRUE_MESSAGE(ai_engine_has(AI_ENGINE_MODEL_PITCH), "no pitch branch built over the image");
    ChainHead head;
    memcpy(&head, pitch_window_start, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY(kChainMagic, head.magic, 4);
    const int16_t *pcm = reinterpret_cast<const int16_t *>(pitch_window_start + sizeof(head));
    const float *want_log_f0 = reinterpret_cast<const float *>(pcm + head.hops * GEN_GRID_HOP_SAMPLES);
    const float *want_sure = want_log_f0 + head.checked;
    const size_t step = ai_engine_pitch_frames();
    TEST_ASSERT_GREATER_THAN(0, step);
    TEST_ASSERT_EQUAL(head.delay, ai_engine_pitch_delay());
    dsp_spec_swift_front_tables_t tables[DSP_SPEC_SWIFT_FRONT_SPECTRA];
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_pitch_tables(tables));
    const size_t bytes = dsp_spec_swift_front_workspace_bytes();
    const size_t frame_values = DSP_SPEC_SWIFT_FRONT_SPECTRA * DSP_SPEC_SWIFT_FRONT_BANDS;
    void *mem = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM);
    float *batch =
        static_cast<float *>(heap_caps_malloc(step * frame_values * sizeof(float), MALLOC_CAP_SPIRAM));
    float *log_f0 = static_cast<float *>(malloc(2 * step * sizeof(float)));
    TEST_ASSERT_TRUE(mem != nullptr && batch != nullptr && log_f0 != nullptr);
    float *sure = log_f0 + step;
    dsp_spec_swift_front_t *front = nullptr;
    TEST_ASSERT_EQUAL(ESP_OK, dsp_spec_swift_front_init(&front, tables, mem, bytes));

    uint32_t off = 0;
    uint32_t seen = 0;
    int64_t front_us = 0;
    int64_t front_peak_us = 0;
    int64_t run_us = 0;
    int64_t run_peak_us = 0;
    int64_t hops_timed = 0;
    int64_t calls_timed = 0;
    for (uint32_t r = 0; r <= head.runs; r++) {
        dsp_spec_swift_front_reset(front);
        ai_engine_pitch_reset();
        size_t filled = 0;
        int64_t taken = 0;
        for (uint32_t h = 0; h < head.hops; h++) {
            bool ready = false;
            const int64_t front_from_us = esp_timer_get_time();
            TEST_ASSERT_EQUAL(ESP_OK, dsp_spec_swift_front_push(front, pcm + h * GEN_GRID_HOP_SAMPLES,
                                                                batch + filled * frame_values, &ready));
            const int64_t front_took_us = esp_timer_get_time() - front_from_us;
            // The first pass is untimed: it pays the caches' first fill.
            if (r > 0) {
                front_us += front_took_us;
                front_peak_us = front_took_us > front_peak_us ? front_took_us : front_peak_us;
                hops_timed++;
            }
            if (!ready || ++filled < step) { continue; }
            const int64_t run_from_us = esp_timer_get_time();
            TEST_ASSERT_EQUAL(ESP_OK, ai_engine_pitch_run(batch, log_f0, sure));
            const int64_t run_took_us = esp_timer_get_time() - run_from_us;
            if (r > 0) {
                run_us += run_took_us;
                run_peak_us = run_took_us > run_peak_us ? run_took_us : run_peak_us;
                calls_timed++;
            } else {
                const int64_t first = taken - static_cast<int64_t>(head.delay);
                off += chain_off(log_f0, want_log_f0, step, first, head.checked, &seen);
                off += chain_off(sure, want_sure, step, first, head.checked, &seen);
            }
            taken += static_cast<int64_t>(step);
            filled = 0;
        }
        vTaskDelay(1);
    }
    printf("%s chain: %" PRIu32 " hops, %u a call: %" PRIu32 " of %" PRIu32 " values off; front %" PRId64
           " us mean, %" PRId64 " us peak a hop; network and decode %" PRId64 " us mean, %" PRId64
           " us peak a call, %" PRId64 " us a hop\n",
           kEntry, head.hops, (unsigned)step, off, seen, front_us / (hops_timed ? hops_timed : 1),
           front_peak_us, run_us / (calls_timed ? calls_timed : 1), run_peak_us,
           run_us / (calls_timed ? calls_timed : 1) / static_cast<int64_t>(step));
    heap_caps_free(mem);
    heap_caps_free(batch);
    free(log_f0);
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                                           STORAGE_MODEL_LABEL_SLOT0);
    TEST_ASSERT_NOT_NULL(part);
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, kSectorBytes));
    TEST_ASSERT_EQUAL(2 * head.checked, seen);
    TEST_ASSERT_EQUAL(0, off);
}

extern "C" void app_main(void)
{
    ESP_ERROR_CHECK(sys_storage_init());
    UNITY_BEGIN();
    if (memcmp(pitch_window_start, kStreamMagic, 4) == 0) {
        unity_run_tests_by_tag("[pitch_stream]", false);
    } else if (memcmp(pitch_window_start, kChainMagic, 4) == 0) {
        unity_run_tests_by_tag("[pitch_chain]", false);
    } else {
        // The whole-graph case erases model slot 0 when it ends, so the cuts run first.
        unity_run_tests_by_tag("[pitch_cuts]", false);
        unity_run_tests_by_tag("[pitch]", false);
    }
    UNITY_END();
}
