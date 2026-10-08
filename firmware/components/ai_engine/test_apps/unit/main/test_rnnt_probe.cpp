#include <inttypes.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ai_engine.h"
#include "command_ctc/ctc_score.h"
#include "command_rnnt/rnnt_search.h"
#include "core/espdl_net.hpp"
#include "core/model_image.hpp"
#include "dl_model_base.hpp"
#include "dsp_spec/pitch.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gen_listen.h"
#include "storage_format.h"
#include "sys_storage.h"
#include "unity.h"

extern const uint8_t rnnt_streams_start[] asm("_binary_rnnt_streams_bin_start");
extern const uint8_t rnnt_vectors_start[] asm("_binary_rnnt_vectors_bin_start");
extern const uint8_t rnnt_windows_start[] asm("_binary_rnnt_windows_bin_start");

namespace {

constexpr size_t kSectorBytes = 4096;
constexpr int kToleranceSteps = 1; // one int8 step, as model->test() allows (KEHOACH 3.14)
constexpr size_t kFeaturesMax = GEN_LISTEN_N_BANDS + DSP_SPEC_PITCH_FEATURES;
constexpr size_t kWidthMax = 512;
constexpr const char *kFrames = "command_rnnt";
constexpr const char *kPredictor = "rnnt_predictor";
constexpr const char *kJoiner = "rnnt_joiner";
constexpr const char *kGateLabel = "voice"; // partitions.csv: rnnt_gate.bin goes there
constexpr size_t kWindowHopsMax = GEN_LISTEN_WINDOW_HOPS;

// Layout written by srpipe.tasks.command.ctc.probe and reused by rnnt/probe.py for the frames graph.
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
static_assert(sizeof(StreamsHead) == 8 && sizeof(StreamRecord) == 40, "rnnt_streams.bin layout");

// Layout of rnnt/probe.py's VECTORS_HEAD; records padded to four bytes follow.
struct __attribute__((packed)) VectorsHead {
    char magic[4];
    uint16_t contexts, pairs;
    uint8_t context, hot_rows;
    uint16_t width, classes;
    int8_t hot_exponent, prefix_exponent, frame_in_exponent, prefix_in_exponent, logits_exponent;
    uint8_t pad[3];
};
static_assert(sizeof(VectorsHead) == 22, "rnnt_vectors.bin layout");

// Layout of the ctc probe's WINDOWS_HEAD, which rnnt/probe.py writes too, then a DECISION_RECORD a window.
struct __attribute__((packed)) WindowsHead {
    char magic[4];
    uint16_t features, windows, reject, margin;
    uint8_t commands, variants_max, units_max, chunk_hops;
};
struct __attribute__((packed)) Decision {
    int16_t command;
    uint16_t score, margin, gap, syllable;
};
static_assert(sizeof(WindowsHead) == 16 && sizeof(Decision) == 10, "rnnt_windows.bin layout");

// Layout of the ctc probe's GATE_HEAD, which rnnt/probe.py's gate_windows writes too.
struct __attribute__((packed)) GateHead {
    char magic[4];
    uint16_t features, windows, reject, margin;
    uint8_t commands, variants_max, units_max, chunk_hops;
    int8_t input_exponent;
    uint8_t pad[3];
};
static_assert(sizeof(GateHead) == 20, "rnnt_gate.bin layout");

ai_engine_lexicon_t s_lexicon;

size_t padded4(size_t n)
{
    return (n + 3) / 4 * 4;
}

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
    return base + padded4(at + commands * (1 + variants_max * (1 + units_max)));
}

void load_command(uint16_t reject, uint16_t margin)
{
    const esp_err_t init = sys_storage_init();
    TEST_ASSERT_TRUE(init == ESP_OK || init == ESP_ERR_INVALID_STATE);
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_REJECT, reject));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN, margin));
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(0),
                              "slot 0 did not load: make ai-unit-rnnt, or the branch's log says why");
    TEST_ASSERT_TRUE_MESSAGE(ai_engine_has(AI_ENGINE_MODEL_COMMAND),
                             "the rnnt branch did not load its entries");
}

void run_test_of(const char *entry)
{
    const ai::Blob blob = ai::image_find(entry, STORAGE_MODEL_KIND_ESPDL);
    TEST_ASSERT_NOT_NULL_MESSAGE(blob.data, entry);
    dl::Model model(reinterpret_cast<const char *>(blob.data), fbs::MODEL_LOCATION_IN_FLASH_RODATA, 0,
                    dl::MEMORY_MANAGER_GREEDY, nullptr, false);
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, model.test(), entry);
}

void build(ai::EspdlNet &net, const char *entry)
{
    const ai::Blob blob = ai::image_find(entry, STORAGE_MODEL_KIND_ESPDL);
    TEST_ASSERT_NOT_NULL_MESSAGE(blob.data, entry);
    TEST_ASSERT_EQUAL(ESP_OK, net.build(blob, entry));
}

// Worst |board - simulation| a step over the frames graph's record; mean us a step.
int stream_frames(ai::EspdlNet &net, const StreamRecord &rec, const int8_t *x, const int8_t *y,
                  int64_t *mean_us)
{
    const ai::Int8Tensor in = net.input();
    const ai::Int8Tensor out = net.output();
    const size_t step_inputs = rec.step_hops * rec.dims;
    int worst = 0;
    int64_t total_us = 0;
    for (uint32_t s = 0; s < rec.steps; s++) {
        memcpy(in.data, x + s * step_inputs, step_inputs);
        const int64_t started_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, net.step());
        total_us += esp_timer_get_time() - started_us;
        for (uint32_t k = 0; k < rec.outputs; k++) {
            const int diff = abs(out.data[k] - y[s * rec.outputs + k]);
            worst = diff > worst ? diff : worst;
        }
        vTaskDelay(1);
    }
    *mean_us = total_us / rec.steps;
    return worst;
}

struct Copied {
    const float *row;
    size_t classes, calls;
};

// The search's log-probabilities without a joiner: every frame and context get the same row.
esp_err_t copied(void *ctx, size_t frame, const uint8_t *contexts, size_t n, float *rows)
{
    (void)frame;
    (void)contexts;
    Copied *c = static_cast<Copied *>(ctx);
    for (size_t j = 0; j < n; j++) {
        memcpy(rows + j * c->classes, c->row, c->classes * sizeof(float));
    }
    c->calls += n;
    return ESP_OK;
}

// One window of raw features hop by hop through the command calls; the score's microseconds in took_us.
void decide(const float *raw, size_t n_features, size_t chunk_hops, uint32_t hops,
            ai_engine_command_result_t *got, int64_t *took_us)
{
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_begin());
    for (uint32_t h = 0; h < hops; h++) {
        TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_step(raw + h * n_features));
        if ((h + 1) % chunk_hops == 0) { vTaskDelay(1); }
    }
    const int64_t started_us = esp_timer_get_time();
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_score(&s_lexicon, got));
    *took_us = esp_timer_get_time() - started_us;
    vTaskDelay(1);
}

} // namespace

TEST_CASE("the rnnt graphs run as their ESP-PPQ simulation does, timed", "[ai_engine][rnnt_graphs]")
{
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(0),
                              "models_0 lacks rnnt_models.bin: run make ai-unit-rnnt");
    for (const char *entry : {kFrames, kPredictor, kJoiner}) {
        run_test_of(entry);
    }

    StreamsHead streams;
    memcpy(&streams, rnnt_streams_start, sizeof(streams));
    TEST_ASSERT_EQUAL_MEMORY("SRCT", streams.magic, 4);
    TEST_ASSERT_EQUAL(1, streams.nets);
    StreamRecord rec;
    memcpy(&rec, rnnt_streams_start + sizeof(streams), sizeof(rec));
    const int8_t *x = reinterpret_cast<const int8_t *>(rnnt_streams_start + sizeof(streams) + sizeof(rec));
    const int8_t *y = x + rec.steps * rec.step_hops * rec.dims;
    static ai::EspdlNet frames, predictor, joiner;
    build(frames, kFrames);
    TEST_ASSERT_EQUAL(rec.step_hops * rec.dims, frames.input().elements);
    TEST_ASSERT_EQUAL(rec.outputs, frames.output().elements);
    int64_t frames_us = 0;
    const int worst_frames = stream_frames(frames, rec, x, y, &frames_us);
    printf("rnnt frames graph streamed %" PRIu32 " steps of %" PRIu32
           " hops: worst |board - simulation| %d, %" PRId64 " us a step\n",
           rec.steps, rec.step_hops, worst_frames, frames_us);
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, worst_frames);

    VectorsHead head;
    memcpy(&head, rnnt_vectors_start, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY("SRRV", head.magic, 4);
    TEST_ASSERT_LESS_OR_EQUAL(kWidthMax, head.width);
    build(predictor, kPredictor);
    build(joiner, kJoiner);
    const ai::Int8Tensor hot = predictor.input();
    const ai::Int8Tensor prefix = predictor.output();
    const ai::Int8Tensor frame_in = joiner.input("frame");
    const ai::Int8Tensor prefix_in = joiner.input("prefix");
    const ai::Int8Tensor logits = joiner.output("logits");
    const size_t columns = frame_in.elements / head.width;
    const bool columns_first = frame_in.dims[1] == columns;
    const bool logits_columns_first = logits.dims[1] == columns;
    printf("rnnt joiner tensors: frame (%u, %u, %u), logits (%u, %u, %u)\n", (unsigned)frame_in.dims[0],
           (unsigned)frame_in.dims[1], (unsigned)frame_in.dims[2], (unsigned)logits.dims[0],
           (unsigned)logits.dims[1], (unsigned)logits.dims[2]);
    TEST_ASSERT_EQUAL(head.hot_rows * head.context, hot.elements);
    TEST_ASSERT_EQUAL(head.width, prefix.elements);
    TEST_ASSERT_EQUAL(columns * head.width, prefix_in.elements);
    TEST_ASSERT_EQUAL(columns * head.classes, logits.elements);
    TEST_ASSERT_EQUAL(0, head.pairs % columns);
    TEST_ASSERT_EQUAL(head.hot_exponent, hot.exponent);
    TEST_ASSERT_EQUAL(head.prefix_exponent, prefix.exponent);
    TEST_ASSERT_EQUAL(head.frame_in_exponent, frame_in.exponent);
    TEST_ASSERT_EQUAL(head.prefix_in_exponent, prefix_in.exponent);
    TEST_ASSERT_EQUAL(head.logits_exponent, logits.exponent);
    const bool rows_first = hot.dims[2] == head.context;
    const long rounded = lrintf(ldexpf(1.0f, -head.hot_exponent));
    TEST_ASSERT_TRUE_MESSAGE(rounded >= 1, "a one-hot 1 rounds to 0 on the predictor's input grid");
    const int8_t one = static_cast<int8_t>(rounded > INT8_MAX ? INT8_MAX : rounded);

    const uint8_t *at = rnnt_vectors_start + sizeof(head);
    int worst_prefix = 0, differ_prefix = 0;
    int64_t predictor_us = 0;
    for (uint16_t k = 0; k < head.contexts; k++) {
        memset(hot.data, 0, hot.elements);
        for (size_t p = 0; p < head.context; p++) {
            hot.data[rows_first ? at[p] * head.context + p : p * head.hot_rows + at[p]] = one;
        }
        const int64_t started_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, predictor.step());
        predictor_us += esp_timer_get_time() - started_us;
        const int8_t *want = reinterpret_cast<const int8_t *>(at + head.context);
        int worst = 0;
        for (size_t d = 0; d < head.width; d++) {
            const int diff = abs(prefix.data[d] - want[d]);
            worst = diff > worst ? diff : worst;
        }
        worst_prefix = worst > worst_prefix ? worst : worst_prefix;
        differ_prefix += worst > 0 ? 1 : 0;
        at += padded4(head.context + head.width);
    }
    int worst_logits = 0, differ_logits = 0;
    int64_t joiner_us = 0;
    const size_t pair_bytes = padded4(2 * head.width + head.classes);
    for (uint16_t k = 0; k < head.pairs; k += columns) {
        for (size_t j = 0; j < columns; j++) {
            const uint8_t *pair = at + j * pair_bytes;
            for (size_t d = 0; d < head.width; d++) {
                const size_t place = columns_first ? j * head.width + d : d * columns + j;
                frame_in.data[place] = static_cast<int8_t>(pair[d]);
                prefix_in.data[place] = static_cast<int8_t>(pair[head.width + d]);
            }
        }
        const int64_t started_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, joiner.step());
        joiner_us += esp_timer_get_time() - started_us;
        for (size_t j = 0; j < columns; j++) {
            const int8_t *want = reinterpret_cast<const int8_t *>(at + j * pair_bytes + 2 * head.width);
            int worst = 0;
            for (size_t c = 0; c < head.classes; c++) {
                const int8_t got = logits.data[logits_columns_first ? j * head.classes + c : c * columns + j];
                const int diff = abs(got - want[c]);
                worst = diff > worst ? diff : worst;
            }
            worst_logits = worst > worst_logits ? worst : worst_logits;
            differ_logits += worst > 0 ? 1 : 0;
        }
        at += columns * pair_bytes;
    }
    printf("rnnt predictor: %u contexts, %d off python, worst %d, %" PRId64 " us a run\n", head.contexts,
           differ_prefix, worst_prefix, predictor_us / head.contexts);
    printf("rnnt joiner: %u pairs, %u a run, %d off python, worst %d, %" PRId64 " us a run\n", head.pairs,
           (unsigned)columns, differ_logits, worst_logits, joiner_us / (head.pairs / columns));
    frames.release();
    predictor.release();
    joiner.release();
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, worst_prefix);
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, worst_logits);
}

// Gate 3 on the chip: every window of the voice partition, rebuilt from its int8 input (KEHOACH 3.12).
TEST_CASE("the rnnt command decides every Gate 3 window of the voice partition as Python decides it, timed",
          "[ai_engine][rnnt_gate]")
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
    if (memcmp(head.magic, "SRRG", 4) != 0) {
        esp_partition_munmap(handle);
        TEST_IGNORE_MESSAGE(
            "voice holds no rnnt Gate 3 record: make ai-unit-rnnt RNNT_RUN=<run> RNNT_ROW=<row>");
    }
    TEST_ASSERT_LESS_OR_EQUAL(kFeaturesMax, head.features);
    load_command(head.reject, head.margin);
    const float *mean = reinterpret_cast<const float *>(base + sizeof(head));
    const float *std = mean + head.features;
    const size_t lexicon_at = sizeof(head) + 2 * head.features * sizeof(float);
    const uint8_t *at = read_lexicon(base, lexicon_at, head.commands, head.variants_max, head.units_max);
    const int64_t prepare_from_us = esp_timer_get_time();
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_prepare(&s_lexicon));
    printf("rnnt prepare: %" PRId64 " us\n", esp_timer_get_time() - prepare_from_us);
    float *raw = static_cast<float *>(
        heap_caps_malloc(kWindowHopsMax * head.features * sizeof(float), MALLOC_CAP_SPIRAM));
    TEST_ASSERT_NOT_NULL(raw);
    const float step = ldexpf(1.0f, head.input_exponent);
    size_t differ = 0;
    int64_t score_total_us = 0, score_peak_us = 0;
    for (uint16_t w = 0; w < head.windows; w++) {
        uint32_t hops;
        memcpy(&hops, at, sizeof(hops));
        TEST_ASSERT_LESS_OR_EQUAL(kWindowHopsMax, hops);
        const int8_t *q = reinterpret_cast<const int8_t *>(at + sizeof(hops));
        for (size_t i = 0; i < hops * head.features; i++) {
            raw[i] = (float)q[i] * step * std[i % head.features] + mean[i % head.features];
        }
        const size_t input_bytes = padded4(hops * head.features);
        Decision want;
        memcpy(&want, at + sizeof(hops) + input_bytes, sizeof(want));
        int64_t took_us = 0;
        ai_engine_command_result_t got;
        decide(raw, head.features, head.chunk_hops, hops, &got, &took_us);
        score_total_us += took_us;
        score_peak_us = took_us > score_peak_us ? took_us : score_peak_us;
        const bool same = got.command == want.command && got.score_permille == want.score &&
                          got.margin_permille == want.margin && got.free_gap_permille == want.gap &&
                          got.syllable_gap_permille == want.syllable;
        differ += same ? 0 : 1;
        printf("gate window %u: board %d %u %u %u, python %d %u %u %u, score %" PRId64 " us\n", w,
               got.command, got.score_permille, got.margin_permille, got.free_gap_permille, want.command,
               want.score, want.margin, want.gap, took_us);
        at += sizeof(hops) + input_bytes + sizeof(want);
    }
    printf("rnnt gate 3: %u windows, %u decided otherwise than python; score %" PRId64 " us mean, %" PRId64
           " us peak\n",
           head.windows, (unsigned)differ, score_total_us / (head.windows ? head.windows : 1), score_peak_us);
    heap_caps_free(raw);
    esp_partition_munmap(handle);
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_KWS, STORAGE_KEY_CMD_REJECT));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN));
    TEST_ASSERT_EQUAL(0, differ);
}

TEST_CASE("the rnnt command calls decide raw feature windows as Python decides their simulation, timed",
          "[ai_engine][rnnt_windows]")
{
    WindowsHead head;
    memcpy(&head, rnnt_windows_start, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY("SRRW", head.magic, 4);
    TEST_ASSERT_LESS_OR_EQUAL(kFeaturesMax, head.features);
    load_command(head.reject, head.margin);
    StreamRecord rec;
    memcpy(&rec, rnnt_streams_start + sizeof(StreamsHead), sizeof(rec));
    VectorsHead vectors;
    memcpy(&vectors, rnnt_vectors_start, sizeof(vectors));
    const uint32_t hops_a_frame = rec.step_hops / (rec.outputs / vectors.width);
    const uint8_t *at =
        read_lexicon(rnnt_windows_start, sizeof(head), head.commands, head.variants_max, head.units_max);
    const int64_t prepare_from_us = esp_timer_get_time();
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_prepare(&s_lexicon));
    printf("rnnt prepare: %" PRId64 " us\n", esp_timer_get_time() - prepare_from_us);
    float *window = static_cast<float *>(
        heap_caps_malloc(kWindowHopsMax * head.features * sizeof(float), MALLOC_CAP_SPIRAM));
    TEST_ASSERT_NOT_NULL(window);
    size_t differ = 0;
    int64_t score_total_us = 0, score_peak_us = 0;
    uint32_t frames_total = 0;
    for (uint16_t w = 0; w < head.windows; w++) {
        uint32_t hops;
        memcpy(&hops, at, sizeof(hops));
        const uint8_t *features = at + sizeof(hops);
        Decision want;
        memcpy(&want, features + hops * head.features * sizeof(float), sizeof(want));
        memcpy(window, features, hops * head.features * sizeof(float));
        ai_engine_command_result_t got;
        int64_t took_us = 0;
        decide(window, head.features, head.chunk_hops, hops, &got, &took_us);
        const uint32_t frames = (hops + hops_a_frame - 1) / hops_a_frame;
        score_total_us += took_us;
        score_peak_us = took_us > score_peak_us ? took_us : score_peak_us;
        frames_total += frames;
        const bool same = got.command == want.command && got.score_permille == want.score &&
                          got.margin_permille == want.margin && got.free_gap_permille == want.gap &&
                          got.syllable_gap_permille == want.syllable;
        differ += same ? 0 : 1;
        printf("rnnt window of %" PRIu32 " hops, %" PRIu32
               " frames: board %d %u %u %u, python %d %u %u %u, score %" PRId64 " us\n",
               hops, frames, got.command, got.score_permille, got.margin_permille, got.free_gap_permille,
               want.command, want.score, want.margin, want.gap, took_us);
        at = features + hops * head.features * sizeof(float) + sizeof(want);
    }
    printf("rnnt command: %u windows, %u decided otherwise than python; score %" PRId64 " us mean, %" PRId64
           " us peak, %" PRId64 " us a frame\n",
           head.windows, (unsigned)differ, score_total_us / head.windows, score_peak_us,
           frames_total ? score_total_us / frames_total : 0);
    heap_caps_free(window);
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_KWS, STORAGE_KEY_CMD_REJECT));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN));
    TEST_ASSERT_EQUAL(0, differ);

    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                                           STORAGE_MODEL_LABEL_SLOT0);
    TEST_ASSERT_NOT_NULL(part);
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, kSectorBytes));
}

TEST_CASE("the rnnt search alone and the log-probabilities of one joiner output, timed",
          "[ai_engine][rnnt_search]")
{
    WindowsHead head;
    memcpy(&head, rnnt_windows_start, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY("SRRW", head.magic, 4);
    read_lexicon(rnnt_windows_start, sizeof(head), head.commands, head.variants_max, head.units_max);
    VectorsHead vectors;
    memcpy(&vectors, rnnt_vectors_start, sizeof(vectors));
    const size_t classes = vectors.classes;
    const size_t tree_bytes = ai_engine_command_rnnt_tree_bytes();
    void *tree = heap_caps_malloc(tree_bytes, MALLOC_CAP_SPIRAM);
    void *work = heap_caps_malloc(ai_engine_command_rnnt_work_bytes(classes), MALLOC_CAP_SPIRAM);
    float *row = static_cast<float *>(heap_caps_malloc(classes * sizeof(float), MALLOC_CAP_SPIRAM));
    TEST_ASSERT_TRUE(tree != nullptr && work != nullptr && row != nullptr);
    int64_t started_us = esp_timer_get_time();
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_rnnt_build(&s_lexicon, tree, tree_bytes));
    const int64_t build_us = esp_timer_get_time() - started_us;

    // A pair's logits from the vectors record: what the joiner hands the log-softmax of the backend.
    const uint8_t *pairs =
        rnnt_vectors_start + sizeof(vectors) + vectors.contexts * padded4(vectors.context + vectors.width);
    const int8_t *logits = reinterpret_cast<const int8_t *>(pairs + 2 * vectors.width);
    constexpr int kRuns = 1000;
    started_us = esp_timer_get_time();
    for (int k = 0; k < kRuns; k++) {
        TEST_ASSERT_EQUAL(ESP_OK,
                          ai_engine_command_ctc_log_probs(logits, vectors.logits_exponent, classes, 1, row));
    }
    const int64_t log_probs_us = esp_timer_get_time() - started_us;

    const size_t frames = (kWindowHopsMax + 1) / 2;
    printf("rnnt search: tree built in %" PRId64 " us; log-softmax of one joiner output %" PRId64 " ns\n",
           build_us, log_probs_us * 1000 / kRuns);
    for (const float beam : {INFINITY, AI_ENGINE_COMMAND_RNNT_BEAM_NATS}) {
        Copied c = {row, classes, 0};
        ai_engine_command_result_t out;
        started_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_rnnt_decide(&s_lexicon, tree, classes, frames, frames,
                                                                static_cast<uint8_t>(classes), beam, copied,
                                                                &c, UINT16_MAX, 0, work, nullptr, &out));
        const int64_t search_us = esp_timer_get_time() - started_us;
        printf("rnnt search at a beam of %.0f nats: %u frames in %" PRId64 " us, %" PRId64
               " us a frame, %u rows asked a frame\n",
               (double)beam, (unsigned)frames, search_us, search_us / (int64_t)frames,
               (unsigned)(c.calls / frames));
    }
    heap_caps_free(tree);
    heap_caps_free(work);
    heap_caps_free(row);
}

extern "C" void app_main(void)
{
    ESP_ERROR_CHECK(sys_storage_init());
    UNITY_BEGIN();
    // The windows case erases model slot 0 when it ends, so it runs last.
    unity_run_tests_by_tag("[rnnt_search]", false);
    unity_run_tests_by_tag("[rnnt_graphs]", false);
    unity_run_tests_by_tag("[rnnt_gate]", false);
    unity_run_tests_by_tag("[rnnt_windows]", false);
    UNITY_END();
}
