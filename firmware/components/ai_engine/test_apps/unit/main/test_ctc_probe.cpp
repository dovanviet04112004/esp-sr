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
constexpr int32_t kMsPerS = 1000;

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

ai_engine_lexicon_t s_lexicon;

// The lexicon the record packs after its head; returns where the windows start, on a four-byte boundary.
const uint8_t *read_lexicon(const WindowsHead &head)
{
    const uint8_t *n_variants = ctc_windows_start + sizeof(head);
    const uint8_t *n_units = n_variants + head.commands;
    const uint8_t *units = n_units + head.commands * head.variants_max;
    s_lexicon.n_commands = head.commands;
    for (size_t c = 0; c < head.commands; c++) {
        s_lexicon.n_variants[c] = n_variants[c];
        for (size_t v = 0; v < head.variants_max; v++) {
            const size_t form = c * head.variants_max + v;
            s_lexicon.variants[c][v] = ai_engine_seq_t{n_units[form], units + form * head.units_max};
        }
    }
    const size_t read = sizeof(head) + head.commands * (1 + head.variants_max * (1 + head.units_max));
    return ctc_windows_start + (read + 3) / 4 * 4;
}

struct Timing {
    int64_t hop_us, chunk_us, chunk_peak_us, score_us, score_peak_us;
    size_t hops, chunks, scores;
};

// One window hop by hop through the command calls; whether its decision is Python's.
bool decide_window(const WindowsHead &head, uint32_t hops, const uint8_t *features, const Decision &want,
                   Timing *t)
{
    float hop[kFeaturesMax];
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_begin());
    for (uint32_t h = 0; h < hops; h++) {
        memcpy(hop, features + h * head.features * sizeof(float), head.features * sizeof(float));
        const int64_t started_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_step(hop));
        const int64_t took_us = esp_timer_get_time() - started_us;
        if ((h + 1) % head.chunk_hops == 0) {
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
    ai_engine_command_result_t got;
    const int64_t started_us = esp_timer_get_time();
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_score(&s_lexicon, &got));
    const int64_t took_us = esp_timer_get_time() - started_us;
    t->score_us += took_us;
    t->score_peak_us = took_us > t->score_peak_us ? took_us : t->score_peak_us;
    t->scores++;
    const bool same = got.command == want.command && got.score_permille == want.score &&
                      got.margin_permille == want.margin && got.free_gap_permille == want.gap;
    printf("ctc window of %" PRIu32 " hops: board %d %u %u %u, python %d %u %u %u\n", hops, got.command,
           got.score_permille, got.margin_permille, got.free_gap_permille, want.command, want.score,
           want.margin, want.gap);
    return same;
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
    const esp_err_t init = sys_storage_init();
    TEST_ASSERT_TRUE(init == ESP_OK || init == ESP_ERR_INVALID_STATE);
    WindowsHead head;
    memcpy(&head, ctc_windows_start, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY("SRCW", head.magic, 4);
    TEST_ASSERT_LESS_OR_EQUAL(kFeaturesMax, head.features);
    for (const char *key : {STORAGE_KEY_CMD_REJECT, STORAGE_KEY_CMD_MARGIN}) {
        const esp_err_t gone = sys_storage_erase(STORAGE_NS_KWS, key);
        TEST_ASSERT_TRUE(gone == ESP_OK || gone == ESP_ERR_NOT_FOUND);
    }
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_load(0));
    TEST_ASSERT_FALSE_MESSAGE(ai_engine_has(AI_ENGINE_MODEL_COMMAND),
                              "the branch must wait for its NVS keys");
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_REJECT, head.reject));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN, head.margin));
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(0), "models_0 lacks ctc_models.bin: run make ai-unit");
    TEST_ASSERT_TRUE(ai_engine_has(AI_ENGINE_MODEL_COMMAND));

    const uint8_t *at = read_lexicon(head);
    Timing t{};
    size_t differ = 0;
    for (uint16_t w = 0; w < head.windows; w++) {
        uint32_t hops;
        memcpy(&hops, at, sizeof(hops));
        const uint8_t *features = at + sizeof(hops);
        Decision want;
        memcpy(&want, features + hops * head.features * sizeof(float), sizeof(want));
        differ += decide_window(head, hops, features, want, &t) ? 0 : 1;
        at = features + hops * head.features * sizeof(float) + sizeof(want);
    }
    printf("ctc command: %u windows, %u decided otherwise than python; %" PRId64 " us a hop, %" PRId64
           " us mean and %" PRId64 " us peak a chunk of %u hops, %" PRId64 " us mean and %" PRId64
           " us peak a score\n",
           head.windows, (unsigned)differ, t.hop_us / (int64_t)(t.hops ? t.hops : 1),
           t.chunk_us / (int64_t)(t.chunks ? t.chunks : 1), t.chunk_peak_us, head.chunk_hops,
           t.score_us / (int64_t)t.scores, t.score_peak_us);
    TEST_ASSERT_EQUAL(0, differ);

    const size_t hop_units = GEN_GRID_HOP_SAMPLES * kMsPerS;
    const size_t hops_max =
        (CONFIG_AI_ENGINE_COMMAND_WINDOW_MS * GEN_GRID_SAMPLE_RATE_HZ + hop_units / 2) / hop_units;
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
