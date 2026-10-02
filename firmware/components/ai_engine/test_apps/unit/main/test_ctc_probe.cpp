#include <inttypes.h>
#include <math.h>
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
#include "gen_listen.h"
#include "sdkconfig.h"
#include "storage_format.h"
#include "sys_storage.h"
#include "unity.h"

extern const uint8_t ctc_streams_start[] asm("_binary_ctc_streams_bin_start");
extern const uint8_t ctc_windows_start[] asm("_binary_ctc_windows_bin_start");

namespace {

constexpr size_t kSectorBytes = 4096;
constexpr int kToleranceSteps = 1; // one int8 step, as model->test() allows (KEHOACH 3.14)
constexpr size_t kNetsMax = 2;
constexpr int64_t kUsPerMs = 1000;
constexpr size_t kFeaturesMax = 64;
constexpr const char *kGateLabel = "voice"; // partitions.csv: ctc_gate.bin goes there

// Layout written by srpipe.tasks.command.ctc.probe; each record's int8 data is padded to four bytes.
struct StreamsHead {
    char magic[4];
    uint32_t nets;
};
struct StreamRecord {
    char entry[STORAGE_MODEL_NAME_BYTES];
    uint32_t step_hops;
    uint32_t dims;
    uint32_t outputs; // int8 values a step: frames a step times channels
    uint32_t steps;
    int32_t input_exponent;
    int32_t output_exponent;
};
static_assert(sizeof(StreamsHead) == 8 && sizeof(StreamRecord) == 40, "ctc_streams.bin layout");

// Layout written by srpipe.tasks.command.ctc.probe's command_windows: WINDOWS_HEAD, then DECISION_RECORD a
// window.
struct __attribute__((packed)) WindowsHead {
    char magic[4];
    uint16_t features, windows, reject, margin;
    uint8_t commands, variants_max, units_max, chunk_hops;
};
struct __attribute__((packed)) Decision {
    int16_t command;
    uint16_t score, margin, gap;
};
static_assert(sizeof(WindowsHead) == 16 && sizeof(Decision) == 8, "ctc_windows.bin layout");

// Layout of the probe's gate_windows: this head, mean and std a feature, the packed lexicon, then a window's
// hops, int8 input padded to four bytes and Decision.
struct __attribute__((packed)) GateHead {
    char magic[4];
    uint16_t features, windows, reject, margin;
    uint8_t commands, variants_max, units_max, chunk_hops;
    int8_t input_exponent;
    uint8_t pad[3];
};
static_assert(sizeof(GateHead) == 20, "ctc_gate.bin layout");

ai_engine_lexicon_t s_lexicon;

// LENH's longest window in hops, the most ai_engine_command_step takes.
size_t window_hops_max()
{
    return GEN_LISTEN_WINDOW_HOPS;
}

// The lexicon packed at base + at; returns where the windows start, on a four-byte boundary of base.
const uint8_t *read_lexicon(const uint8_t *base, size_t at, uint8_t commands, uint8_t variants_max,
                            uint8_t units_max)
{
    const uint8_t *n_variants = base + at;
    const uint8_t *n_units = n_variants + commands;
    const uint8_t *units = n_units + commands * variants_max;
    s_lexicon.n_commands = commands;
    for (size_t c = 0; c < commands; c++) {
        s_lexicon.n_variants[c] = n_variants[c];
        for (size_t v = 0; v < variants_max; v++) {
            const size_t form = c * variants_max + v;
            s_lexicon.variants[c][v] = ai_engine_seq_t{n_units[form], units + form * units_max};
        }
    }
    const size_t read = at + commands * (1 + variants_max * (1 + units_max));
    return base + (read + 3) / 4 * 4;
}

struct Timing {
    int64_t hop_us, chunk_us, chunk_peak_us, score_us, score_peak_us;
    size_t hops, chunks, scores;
};

// One window of n_features a hop, hop by hop through the command calls; whether its decision is Python's.
bool decide_window(size_t n_features, size_t chunk_hops, uint32_t hops, const uint8_t *features,
                   const Decision &want, Timing *t, ai_engine_command_result_t *got)
{
    float hop[kFeaturesMax];
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_begin());
    for (uint32_t h = 0; h < hops; h++) {
        memcpy(hop, features + h * n_features * sizeof(float), n_features * sizeof(float));
        const int64_t started_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_step(hop));
        const int64_t took_us = esp_timer_get_time() - started_us;
        if ((h + 1) % chunk_hops == 0) {
            t->chunk_us += took_us;
            t->chunk_peak_us = took_us > t->chunk_peak_us ? took_us : t->chunk_peak_us;
            t->chunks++;
            // Let IDLE0 run between chunks: seconds of them on core 0 trip the task watchdog.
            vTaskDelay(1);
        } else {
            t->hop_us += took_us;
            t->hops++;
        }
    }
    const int64_t started_us = esp_timer_get_time();
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_score(&s_lexicon, got));
    const int64_t took_us = esp_timer_get_time() - started_us;
    t->score_us += took_us;
    t->score_peak_us = took_us > t->score_peak_us ? took_us : t->score_peak_us;
    t->scores++;
    return got->command == want.command && got->score_permille == want.score &&
           got->margin_permille == want.margin && got->free_gap_permille == want.gap;
}

void print_timing(const char *what, uint16_t windows, size_t differ, const Timing &t, uint8_t chunk_hops)
{
    printf("%s: %u windows, %u decided otherwise than python; %" PRId64 " us a hop, %" PRId64
           " us mean and %" PRId64 " us peak a chunk of %u hops, %" PRId64 " us mean and %" PRId64
           " us peak a score\n",
           what, windows, (unsigned)differ, t.hop_us / (int64_t)(t.hops ? t.hops : 1),
           t.chunk_us / (int64_t)(t.chunks ? t.chunks : 1), t.chunk_peak_us, chunk_hops,
           t.score_us / (int64_t)(t.scores ? t.scores : 1), t.score_peak_us);
}

// The command branch loaded from slot 0 with reject and margin seeded into NVS, which it waits for.
void load_command(uint16_t reject, uint16_t margin)
{
    const esp_err_t init = sys_storage_init();
    TEST_ASSERT_TRUE(init == ESP_OK || init == ESP_ERR_INVALID_STATE);
    for (const char *key : {STORAGE_KEY_CMD_REJECT, STORAGE_KEY_CMD_MARGIN}) {
        const esp_err_t gone = sys_storage_erase(STORAGE_NS_KWS, key);
        TEST_ASSERT_TRUE(gone == ESP_OK || gone == ESP_ERR_NOT_FOUND);
    }
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_load(0));
    TEST_ASSERT_FALSE_MESSAGE(ai_engine_has(AI_ENGINE_MODEL_COMMAND),
                              "the branch must wait for its NVS keys");
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_REJECT, reject));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN, margin));
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(0), "models_0 lacks ctc_models.bin: run make ai-unit");
    TEST_ASSERT_TRUE(ai_engine_has(AI_ENGINE_MODEL_COMMAND));
}

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

// Ahead of the streaming case, which erases model slot 0 when it ends.
TEST_CASE("the ctc command calls decide raw feature windows as Python decides their simulation, timed",
          "[ai_engine]")
{
    WindowsHead head;
    memcpy(&head, ctc_windows_start, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY("SRCW", head.magic, 4);
    TEST_ASSERT_LESS_OR_EQUAL(kFeaturesMax, head.features);
    load_command(head.reject, head.margin);

    const uint8_t *at =
        read_lexicon(ctc_windows_start, sizeof(head), head.commands, head.variants_max, head.units_max);
    Timing t{};
    size_t differ = 0;
    for (uint16_t w = 0; w < head.windows; w++) {
        uint32_t hops;
        memcpy(&hops, at, sizeof(hops));
        const uint8_t *features = at + sizeof(hops);
        Decision want;
        memcpy(&want, features + hops * head.features * sizeof(float), sizeof(want));
        ai_engine_command_result_t got;
        differ += decide_window(head.features, head.chunk_hops, hops, features, want, &t, &got) ? 0 : 1;
        printf("ctc window of %" PRIu32 " hops: board %d %u %u %u, python %d %u %u %u\n", hops, got.command,
               got.score_permille, got.margin_permille, got.free_gap_permille, want.command, want.score,
               want.margin, want.gap);
        at = features + hops * head.features * sizeof(float) + sizeof(want);
    }
    print_timing("ctc command", head.windows, differ, t, head.chunk_hops);
    TEST_ASSERT_EQUAL(0, differ);

    const size_t hops_max = window_hops_max();
    const float zeros[kFeaturesMax] = {};
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_begin());
    for (size_t h = 0; h < hops_max; h++) {
        TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_step(zeros));
        if ((h + 1) % head.chunk_hops == 0) { vTaskDelay(1); }
    }
    TEST_ASSERT_EQUAL(ESP_ERR_NO_MEM, ai_engine_command_step(zeros));
    ai_engine_command_result_t last;
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_score(&s_lexicon, &last));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, ai_engine_command_score(&s_lexicon, &last));

    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_KWS, STORAGE_KEY_CMD_REJECT));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN));
}

// Gate 3 on the chip: every window of the voice partition, rebuilt from its int8 input (KEHOACH 3.12).
TEST_CASE("the ctc command decides every Gate 3 window of the voice partition as Python decides it, timed",
          "[ai_engine]")
{
    const esp_partition_t *part =
        esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, kGateLabel);
    TEST_ASSERT_NOT_NULL(part);
    const void *mapped = nullptr;
    esp_partition_mmap_handle_t handle;
    TEST_ASSERT_EQUAL(ESP_OK,
                      esp_partition_mmap(part, 0, part->size, ESP_PARTITION_MMAP_DATA, &mapped, &handle));
    const uint8_t *base = static_cast<const uint8_t *>(mapped);
    GateHead head;
    memcpy(&head, base, sizeof(head));
    if (memcmp(head.magic, "SRCG", 4) != 0) {
        esp_partition_munmap(handle);
        TEST_IGNORE_MESSAGE("voice holds no Gate 3 record: make ai-unit CTC_RUN=<run> CTC_ROW=<row>");
    }
    TEST_ASSERT_LESS_OR_EQUAL(kFeaturesMax, head.features);
    load_command(head.reject, head.margin);
    const float *mean = reinterpret_cast<const float *>(base + sizeof(head));
    const float *std = mean + head.features;
    const size_t lexicon_at = sizeof(head) + 2 * head.features * sizeof(float);
    const uint8_t *at = read_lexicon(base, lexicon_at, head.commands, head.variants_max, head.units_max);
    const size_t window_floats = window_hops_max() * head.features;
    float *raw = static_cast<float *>(heap_caps_malloc(window_floats * sizeof(float), MALLOC_CAP_SPIRAM));
    TEST_ASSERT_NOT_NULL(raw);
    const float step = ldexpf(1.0f, head.input_exponent);
    Timing t{};
    size_t differ = 0;
    for (uint16_t w = 0; w < head.windows; w++) {
        uint32_t hops;
        memcpy(&hops, at, sizeof(hops));
        TEST_ASSERT_LESS_OR_EQUAL(window_hops_max(), hops);
        const int8_t *q = reinterpret_cast<const int8_t *>(at + sizeof(hops));
        for (size_t i = 0; i < hops * head.features; i++) {
            raw[i] = (float)q[i] * step * std[i % head.features] + mean[i % head.features];
        }
        const size_t input_bytes = (hops * head.features + 3) / 4 * 4;
        Decision want;
        memcpy(&want, at + sizeof(hops) + input_bytes, sizeof(want));
        ai_engine_command_result_t got;
        const uint8_t *features = reinterpret_cast<const uint8_t *>(raw);
        differ += decide_window(head.features, head.chunk_hops, hops, features, want, &t, &got) ? 0 : 1;
        printf("gate window %u: board %d %u %u %u, python %d %u %u %u\n", w, got.command, got.score_permille,
               got.margin_permille, got.free_gap_permille, want.command, want.score, want.margin, want.gap);
        at += sizeof(hops) + input_bytes + sizeof(want);
    }
    print_timing("ctc gate 3", head.windows, differ, t, head.chunk_hops);
    heap_caps_free(raw);
    esp_partition_munmap(handle);
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_KWS, STORAGE_KEY_CMD_REJECT));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN));
    TEST_ASSERT_EQUAL(0, differ);
}

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
