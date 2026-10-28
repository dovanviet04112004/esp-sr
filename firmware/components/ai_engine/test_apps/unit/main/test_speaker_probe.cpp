#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ai_engine.h"
#include "app_events.h"
#include "core/espdl/runtime/espdl_loan.hpp"
#include "core/espdl/espdl_ops.hpp"
#include "core/espdl/runtime/espdl_split.hpp"
#include "core/espdl_net.hpp"
#include "core/model_image.hpp"
#include "dl_model_base.hpp"
#include "dsp_spec/spk_fbank.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gen_speaker.h"
#include "storage_format.h"
#include "sys_storage.h"
#include "unity.h"

extern const uint8_t speaker_windows_start[] asm("_binary_speaker_windows_bin_start");

namespace {

constexpr size_t kSectorBytes = 4096;
constexpr int kToleranceSteps = 0; // bit for bit, as model->test() asks of int8 (KEHOACH 3.14)
constexpr uint32_t kShownValues = 8;
constexpr int kCutsMax = 16;
constexpr const char *kChainLabel = "voice"; // make ai-unit-speaker CHAIN=1 writes it
constexpr int kLoadedRuns = 3;               // windows embedded while core 1 writes flash
constexpr uint32_t kLoadStackBytes = 4096;
constexpr UBaseType_t kLoadPriority = 2;
constexpr uint32_t kLoadGapMs = 10; // between NVS writes, sparing the flash
constexpr uint32_t kHelperStackBytes = 4096;
constexpr UBaseType_t kHelperPriority = 1; // phu_task's, under the app's other tasks (KEHOACH 5.2)
constexpr uint32_t kHelperWaitMs = 100;
constexpr uint32_t kInternalCaps = MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT;
constexpr int kCapBlocksMax = 32; // blocks held to cap internal RAM, at most

// Layout written by srpipe.tasks.speaker.probe; the record's int8 data is padded to four bytes.
struct WindowsHead {
    char magic[4];
    uint32_t runs;
};
struct WindowRecord {
    char entry[STORAGE_MODEL_NAME_BYTES];
    uint32_t frames;
    uint32_t mels;
    uint32_t dims;
    int32_t input_exponent;
    int32_t output_exponent;
};
static_assert(sizeof(WindowsHead) == 8 && sizeof(WindowRecord) == 36, "speaker_windows.bin layout");

// Layout written by srpipe.tasks.speaker.probe --chain: the head, the voiceprints, their means and
// deviations, the members' enrolment units, then each window.
struct ChainHead {
    char magic[4];
    uint32_t windows; // with PCM
    uint32_t dims;
    uint32_t prints;   // the owner's, then a member's each
    uint32_t enrolled; // windows a voiceprint; the owner's are the first ones
};
struct ChainWindow {
    uint32_t kind; // ChainKind
    int32_t milli_z;
    int32_t best; // the voiceprint giving the score
    int32_t passes;
    int32_t cosine_permille; // of that voiceprint
    float mean;              // the window's own over the cohort's top
    float std;
};
static_assert(sizeof(ChainHead) == 20 && sizeof(ChainWindow) == 28, "speaker_chain.bin layout");
enum ChainKind : uint32_t { kEnrol = 0, kOwner, kImpostor, kMember };

#if CONFIG_AI_ENGINE_CORES_TWO
void serve(void *)
{
    for (;;) {
        ai_engine_helper_serve(kHelperWaitMs);
    }
}

// The app's own phu_task on core 1, as main's task table makes it.
void start_helper()
{
    static TaskHandle_t helper = nullptr;
    if (helper != nullptr) { return; }
    TEST_ASSERT_EQUAL(pdPASS, xTaskCreatePinnedToCore(serve, "phu_task", kHelperStackBytes, nullptr,
                                                      kHelperPriority, &helper, 1));
}
#else
void start_helper()
{}
#endif

esp_err_t test_of(const ai::Blob &blob, bool profiled)
{
    ai::register_espdl_ops();
    dl::Model model(reinterpret_cast<const char *>(blob.data), fbs::MODEL_LOCATION_IN_FLASH_RODATA, 0,
                    dl::MEMORY_MANAGER_GREEDY, nullptr, false);
    // One run builds every module, so the test then runs on the ops' loan as EspdlNet::step does.
    (void)ai::loan_take_asked();
    model.run();
    // Two runs back to back starve IDLE0 past the task watchdog, whose report lands inside a module's time.
    vTaskDelay(1);
    const ai::WeightLoan loan(ai::loan_take_asked());
    if (profiled) { model.profile_module(true); }
    return model.test();
}

// Core 1's NVS writes of a threshold, two values in turn, as an MQTT threshold change makes them.
struct FlashLoad {
    volatile bool stop = false;
    volatile bool done = false;
    uint16_t first = 0;
    uint32_t writes = 0;
    esp_err_t err = ESP_OK;
};

void write_flash(void *arg)
{
    auto *load = static_cast<FlashLoad *>(arg);
    for (uint16_t flip = 1; !load->stop && load->err == ESP_OK; flip ^= 1) {
        load->err = sys_storage_set_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN,
                                        static_cast<uint16_t>(load->first ^ flip));
        load->writes++;
        vTaskDelay(pdMS_TO_TICKS(kLoadGapMs));
    }
    load->done = true;
    vTaskDelete(nullptr);
}

// The window's worst step off the simulation over runs embedded while core 1 writes flash; flash writes wait
// out core 0's dcache preloads (ADR-0026).
int worst_under_flash_writes(ai::EspdlNet &net, const int8_t *x, const int8_t *y, uint32_t dims)
{
    FlashLoad load;
    const bool had = sys_storage_get_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN, &load.first) == ESP_OK;
    TEST_ASSERT_EQUAL(pdPASS, xTaskCreatePinnedToCore(write_flash, "flash_load", kLoadStackBytes, &load,
                                                      kLoadPriority, nullptr, 1));
    const ai::Int8Tensor in = net.input();
    const ai::Int8Tensor out = net.output();
    int worst = 0;
    int64_t total_us = 0;
    for (int r = 0; r < kLoadedRuns; r++) {
        memcpy(in.data, x, in.elements);
        const int64_t started_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, net.step());
        total_us += esp_timer_get_time() - started_us;
        for (uint32_t k = 0; k < dims; k++) {
            const int diff = abs(out.data[k] - y[k]);
            worst = diff > worst ? diff : worst;
        }
        vTaskDelay(1);
    }
    load.stop = true;
    while (!load.done) {
        vTaskDelay(1);
    }
    const esp_err_t restored = had ? sys_storage_set_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN, load.first)
                                   : sys_storage_erase(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN);
    printf("speaker: %d windows while core 1 wrote NVS %" PRIu32
           " times: worst |board - simulation| %d, %" PRId64 " us mean a window\n",
           kLoadedRuns, load.writes, worst, total_us / kLoadedRuns);
    TEST_ASSERT_EQUAL(ESP_OK, load.err);
    TEST_ASSERT_EQUAL(ESP_OK, restored);
    TEST_ASSERT_GREATER_THAN(0, load.writes);
    return worst;
}

// Internal RAM held until its largest free block is at most cap bytes, as on the product once dsp_afe runs:
// the count of blocks held.
int cap_internal(size_t cap, void **held)
{
    int n = 0;
    for (size_t largest = heap_caps_get_largest_free_block(kInternalCaps); largest > cap && n < kCapBlocksMax;
         largest = heap_caps_get_largest_free_block(kInternalCaps)) {
        held[n] = heap_caps_malloc(largest - cap, kInternalCaps);
        if (held[n] == nullptr) { break; }
        n++;
    }
    return n;
}

} // namespace

TEST_CASE("the speaker graph embeds one window as its ESP-PPQ simulation does, timed", "[ai_engine][speaker]")
{
    start_helper();
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(0),
                              "models_0 lacks speaker_models.bin: run make ai-unit-speaker");
    WindowsHead head;
    memcpy(&head, speaker_windows_start, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY("SRSV", head.magic, 4);
    TEST_ASSERT_GREATER_THAN(0, head.runs);
    WindowRecord rec;
    memcpy(&rec, speaker_windows_start + sizeof(head), sizeof(rec));
    const int8_t *x = reinterpret_cast<const int8_t *>(speaker_windows_start + sizeof(head) + sizeof(rec));
    const int8_t *y = x + rec.frames * rec.mels;

    const ai::Blob blob = ai::image_find(rec.entry, STORAGE_MODEL_KIND_ESPDL);
    TEST_ASSERT_NOT_NULL_MESSAGE(blob.data, rec.entry);
    static ai::EspdlNet net;
    const size_t psram_before = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    TEST_ASSERT_EQUAL(ESP_OK, net.build(blob, rec.entry));
    const size_t psram_bytes = psram_before - heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    const ai::Int8Tensor in = net.input();
    const ai::Int8Tensor out = net.output();
    TEST_ASSERT_EQUAL(rec.frames * rec.mels, in.elements);
    TEST_ASSERT_EQUAL(rec.dims, out.elements);
    TEST_ASSERT_EQUAL(rec.input_exponent, in.exponent);
    TEST_ASSERT_EQUAL(rec.output_exponent, out.exponent);
    void *held[kCapBlocksMax] = {};
    const int capped = UNIT_LOAN_CAP_BYTES > 0 ? cap_internal(UNIT_LOAN_CAP_BYTES, held) : 0;
    if (UNIT_LOAN_CAP_BYTES > 0) {
        printf("%s: internal RAM held in %d blocks, largest free %u B\n", rec.entry, capped,
               (unsigned)heap_caps_get_largest_free_block(kInternalCaps));
    }

    int worst = 0;
    uint32_t off = 0;
    int64_t total_us = 0;
    int64_t peak_us = 0;
    for (uint32_t r = 0; r <= head.runs; r++) {
        memcpy(in.data, x, in.elements);
        const int64_t started_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, net.step());
        const int64_t took_us = esp_timer_get_time() - started_us;
        for (uint32_t k = 0; k < rec.dims; k++) {
            const int diff = abs(out.data[k] - y[k]);
            worst = diff > worst ? diff : worst;
            off += r == 0 && diff > kToleranceSteps;
        }
        // The first run is untimed: it pays the caches' first fill.
        if (r > 0) {
            total_us += took_us;
            peak_us = took_us > peak_us ? took_us : peak_us;
        }
        vTaskDelay(1);
    }
    printf("%s: %" PRIu32 " frames x %" PRIu32 " mels: worst |board - simulation| %d, %" PRId64
           " us mean, %" PRId64 " us peak a window over %" PRIu32
           " runs, psram %u B with its tensors, %d op parts\n",
           rec.entry, rec.frames, rec.mels, worst, total_us / head.runs, peak_us, head.runs,
           (unsigned)psram_bytes, ai::split_parts());
    printf("%s: %" PRIu32 " of %" PRIu32 " values off the simulation; first board/simulation:", rec.entry,
           off, rec.dims);
    for (uint32_t k = 0; k < rec.dims && k < kShownValues; k++) {
        printf(" %d/%d", out.data[k], y[k]);
    }
    printf("\n");
    const int loaded_worst = worst_under_flash_writes(net, x, y, rec.dims);
    net.release();
    const esp_err_t tested = test_of(blob, true);
    for (int k = 0; k < capped; k++) {
        heap_caps_free(held[k]);
    }
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                                           STORAGE_MODEL_LABEL_SLOT0);
    TEST_ASSERT_NOT_NULL(part);
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, kSectorBytes));
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, tested, rec.entry);
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, worst);
    TEST_ASSERT_LESS_OR_EQUAL(kToleranceSteps, loaded_worst);
}

namespace {

constexpr size_t kLineTestBytes = 256 * 1024; // four times the dcache, so lines leave and come back
constexpr int kLineTestRounds = 64;
constexpr size_t kLineBytes = CONFIG_ESP32S3_DATA_CACHE_LINE_SIZE;
constexpr int kCores = 2;

// One core's half of every cache line of a PSRAM buffer, written then read back each round.
struct HalfLines {
    uint8_t *buf = nullptr;
    size_t half = 0;
    volatile bool done = false;
    uint32_t wrong = 0;
};

void write_half_lines(void *arg)
{
    auto *h = static_cast<HalfLines *>(arg);
    for (int r = 0; r < kLineTestRounds; r++) {
        const auto value = static_cast<uint8_t>(kCores * r + h->half);
        alignas(16) uint8_t pattern[16];
        memset(pattern, value, sizeof(pattern));
        const uint8_t *from = pattern;
        asm volatile("ee.vld.128.ip q0, %0, 0" : "+r"(from) : : "memory");
        for (size_t at = h->half * kLineBytes / 2; at < kLineTestBytes; at += kLineBytes) {
            for (size_t v = 0; v < kLineBytes / 2; v += sizeof(pattern)) {
                uint8_t *to = h->buf + at + v;
                if (r % 2 == 0) {
                    asm volatile("ee.vst.128.ip q0, %0, 0" : "+r"(to) : : "memory");
                } else {
                    memset(to, value, sizeof(pattern));
                }
            }
        }
        for (size_t at = h->half * kLineBytes / 2; at < kLineTestBytes; at += kLineBytes) {
            for (size_t i = 0; i < kLineBytes / 2; i++) {
                h->wrong += h->buf[at + i] != value;
            }
        }
    }
    h->done = true;
    vTaskDelete(nullptr);
}

} // namespace

TEST_CASE("two cores writing either half of every PSRAM cache line keep each other's bytes",
          "[ai_engine][psram_lines]")
{
    auto *buf =
        static_cast<uint8_t *>(heap_caps_aligned_alloc(kLineBytes, kLineTestBytes, MALLOC_CAP_SPIRAM));
    TEST_ASSERT_NOT_NULL(buf);
    HalfLines halves[kCores];
    for (int c = 0; c < kCores; c++) {
        halves[c].buf = buf;
        halves[c].half = c;
        TEST_ASSERT_EQUAL(pdPASS, xTaskCreatePinnedToCore(write_half_lines, "half_lines", kLoadStackBytes,
                                                          &halves[c], kLoadPriority, nullptr, c));
    }
    while (!halves[0].done || !halves[1].done) {
        vTaskDelay(1);
    }
    heap_caps_free(buf);
    printf("psram lines: %" PRIu32 " and %" PRIu32 " bytes lost over %d rounds of %u KB\n", halves[0].wrong,
           halves[1].wrong, kLineTestRounds, static_cast<unsigned>(kLineTestBytes / 1024));
    TEST_ASSERT_EQUAL(0, halves[0].wrong + halves[1].wrong);
}

TEST_CASE("each cut of the speaker graph runs as its ESP-PPQ simulation does", "[ai_engine][speaker_cuts]")
{
    start_helper();
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(0),
                              "models_0 lacks speaker_models.bin: run make ai-unit-speaker");
    int differing = 0;
    for (int i = 0; i < kCutsMax; i++) {
        char entry[STORAGE_MODEL_NAME_BYTES];
        snprintf(entry, sizeof(entry), "speaker_c%d", i);
        const ai::Blob blob = ai::image_find(entry, STORAGE_MODEL_KIND_ESPDL);
        if (blob.data == nullptr) { break; }
        const bool matches = test_of(blob, false) == ESP_OK;
        printf("%s: model->test() %s the simulation\n", entry, matches ? "matches" : "differs from");
        differing += !matches;
    }
    TEST_ASSERT_EQUAL(0, differing);
}

namespace {

struct ChainTiming {
    int64_t first_us = 0;
    int64_t first_internal_bytes = 0; // heap the first check after load keeps
    int64_t first_psram_bytes = 0;
    int64_t embed_us = 0; // the windows after the first
    int64_t embed_peak_us = 0;
    int64_t front_us = 0;
};

int64_t free_bytes(uint32_t caps)
{
    return static_cast<int64_t>(heap_caps_get_free_size(caps));
}

// Every window's unit embedding from its PCM, as xac_task gets it; the front end alone is timed beside.
uint32_t embed_all(const uint8_t *at, const ChainHead &head, size_t dims, float *units, ChainTiming &t)
{
    const dsp_spec_spk_fbank_config_t cfg = GEN_SPEAKER_FBANK_CONFIG;
    const size_t fb_bytes = dsp_spec_spk_fbank_workspace_bytes(&cfg);
    void *fb_mem = heap_caps_malloc(fb_bytes, MALLOC_CAP_SPIRAM);
    auto *features = static_cast<float *>(
        heap_caps_malloc(GEN_SPEAKER_N_FRAMES * GEN_SPEAKER_N_BANDS * sizeof(float), MALLOC_CAP_SPIRAM));
    auto *pcm = static_cast<int16_t *>(
        heap_caps_malloc(GEN_SPEAKER_WINDOW_SAMPLES * sizeof(int16_t), MALLOC_CAP_SPIRAM));
    dsp_spec_spk_fbank_t *fb = nullptr;
    TEST_ASSERT_EQUAL(ESP_OK, dsp_spec_spk_fbank_init(&fb, &cfg, fb_mem, fb_bytes));
    const size_t record =
        sizeof(ChainWindow) + GEN_SPEAKER_WINDOW_SAMPLES * sizeof(int16_t) + dims * sizeof(float);
    uint32_t off = 0;
    for (uint32_t i = 0; i < head.windows; i++) {
        const uint8_t *w = at + i * record;
        memcpy(pcm, w + sizeof(ChainWindow), GEN_SPEAKER_WINDOW_SAMPLES * sizeof(int16_t));
        int64_t from_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, dsp_spec_spk_fbank_run(fb, pcm, features));
        t.front_us += esp_timer_get_time() - from_us;
        const int64_t internal_before = free_bytes(MALLOC_CAP_INTERNAL);
        const int64_t psram_before = free_bytes(MALLOC_CAP_SPIRAM);
        from_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, ai_engine_speaker_embed(pcm, units + i * dims));
        const int64_t took_us = esp_timer_get_time() - from_us;
        if (i == 0) {
            t.first_us = took_us;
            t.first_internal_bytes = internal_before - free_bytes(MALLOC_CAP_INTERNAL);
            t.first_psram_bytes = psram_before - free_bytes(MALLOC_CAP_SPIRAM);
        } else {
            t.embed_us += took_us;
            t.embed_peak_us = took_us > t.embed_peak_us ? took_us : t.embed_peak_us;
        }
        const uint8_t *want = w + sizeof(ChainWindow) + GEN_SPEAKER_WINDOW_SAMPLES * sizeof(int16_t);
        off += memcmp(units + i * dims, want, dims * sizeof(float)) != 0;
        vTaskDelay(1);
    }
    heap_caps_free(fb_mem);
    heap_caps_free(features);
    heap_caps_free(pcm);
    return off;
}

} // namespace

TEST_CASE("the speaker chain enrols a full roster and scores every window against it as Python does, timed",
          "[ai_engine][speaker_chain]")
{
    start_helper();
    const esp_partition_t *part =
        esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, kChainLabel);
    TEST_ASSERT_NOT_NULL(part);
    const void *mapped = nullptr;
    esp_partition_mmap_handle_t handle;
    TEST_ASSERT_EQUAL(ESP_OK,
                      esp_partition_mmap(part, 0, part->size, ESP_PARTITION_MMAP_DATA, &mapped, &handle));
    const auto *bytes = static_cast<const uint8_t *>(mapped);
    ChainHead head;
    memcpy(&head, bytes, sizeof(head));
    if (memcmp(head.magic, "SRSA", sizeof(head.magic)) != 0) {
        esp_partition_munmap(handle);
        TEST_IGNORE_MESSAGE(
            "voice holds no speaker roster chain: make ai-unit-speaker RUN=<run> ROW=<row> CHAIN=1");
    }
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_load(0));
    TEST_ASSERT_TRUE(ai_engine_has(AI_ENGINE_MODEL_SPEAKER));
    const size_t dims = ai_engine_speaker_dims();
    TEST_ASSERT_EQUAL(head.dims, dims);
    TEST_ASSERT_EQUAL(APP_SPEAKERS_MAX, head.prints);
    auto *units =
        static_cast<float *>(heap_caps_malloc(head.windows * dims * sizeof(float), MALLOC_CAP_SPIRAM));
    auto *prints =
        static_cast<float *>(heap_caps_malloc(head.prints * dims * sizeof(float), MALLOC_CAP_SPIRAM));
    TEST_ASSERT_NOT_NULL(units);
    TEST_ASSERT_NOT_NULL(prints);
    const auto *python_prints = reinterpret_cast<const float *>(bytes + sizeof(head));
    const auto *python_norms = python_prints + head.prints * dims;
    const auto *member_units = python_norms + 2 * head.prints;
    const uint8_t *at =
        reinterpret_cast<const uint8_t *>(member_units + (head.prints - 1) * head.enrolled * dims);
    ChainTiming t;
    const uint32_t units_off = embed_all(at, head, dims, units, t);
    ai_engine_speaker_voiceprint(units, head.enrolled, prints);
    for (uint32_t k = 1; k < head.prints; k++) {
        ai_engine_speaker_voiceprint(member_units + (k - 1) * head.enrolled * dims, head.enrolled,
                                     prints + k * dims);
    }
    const bool prints_same = memcmp(prints, python_prints, head.prints * dims * sizeof(float)) == 0;
    float means[APP_SPEAKERS_MAX], stds[APP_SPEAKERS_MAX];
    uint32_t norms_off = 0;
    int64_t norm_us = 0, score_us = 0, score_peak_us = 0;
    for (uint32_t k = 0; k < head.prints; k++) {
        const int64_t from_us = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, ai_engine_speaker_norm(prints + k * dims, &means[k], &stds[k]));
        norm_us += esp_timer_get_time() - from_us;
        norms_off += memcmp(&means[k], &python_norms[2 * k], sizeof(float)) != 0 ||
                     memcmp(&stds[k], &python_norms[2 * k + 1], sizeof(float)) != 0;
    }
    const size_t record =
        sizeof(ChainWindow) + GEN_SPEAKER_WINDOW_SAMPLES * sizeof(int16_t) + dims * sizeof(float);
    uint32_t off = 0, tried[4] = {0}, passed[4] = {0}, theirs[4] = {0}, own_norms_off = 0;
    for (uint32_t i = 0; i < head.windows; i++) {
        ChainWindow want;
        memcpy(&want, at + i * record, sizeof(want));
        if (want.kind == kEnrol) { continue; }
        float mean = 0.0f, std = 0.0f;
        TEST_ASSERT_EQUAL(ESP_OK, ai_engine_speaker_norm(units + i * dims, &mean, &std));
        own_norms_off +=
            memcmp(&mean, &want.mean, sizeof(float)) != 0 || memcmp(&std, &want.std, sizeof(float)) != 0;
        size_t best = 0;
        int16_t cosine = 0;
        const int64_t from_us = esp_timer_get_time();
        const int16_t score =
            ai_engine_speaker_best(units + i * dims, prints, means, stds, head.prints, &best, &cosine);
        const int64_t took_us = esp_timer_get_time() - from_us;
        score_us += took_us;
        score_peak_us = took_us > score_peak_us ? took_us : score_peak_us;
        const bool passes = ai_engine_speaker_passes(score, head.prints, AI_ENGINE_SPEAKER_NOISE_BINS - 1);
        off += score != want.milli_z || (int32_t)best != want.best || passes != (want.passes != 0) ||
               cosine != want.cosine_permille;
        tried[want.kind]++;
        passed[want.kind] += passes;
        // Members test in roster order, so the n-th member's window belongs to voiceprint n.
        const uint32_t own = want.kind == kMember ? tried[kMember] : 0;
        theirs[want.kind] += (want.kind == kOwner || want.kind == kMember) && best == own;
        printf("speaker chain window %" PRIu32 ", kind %" PRIu32
               ": best %d milli-z of voiceprint %u, cosine %d, "
               "python %" PRId32 " of %" PRId32 ", cosine %" PRId32 ", %s\n",
               i, want.kind, score, (unsigned)best, cosine, want.milli_z, want.best, want.cosine_permille,
               passes ? "passes" : "stopped");
    }
    printf("speaker chain: %" PRIu32 " windows, %" PRIu32 " units, %" PRIu32 " voiceprint norms, %" PRIu32
           " window norms and %" PRIu32 " decisions off python, %" PRIu32
           " voiceprints %s; at %d milli-z owner %" PRIu32 "/%" PRIu32 " (%" PRIu32
           " best on their own), member %" PRIu32 "/%" PRIu32 " (%" PRIu32
           " best on their own), impostor %" PRIu32 "/%" PRIu32 " passed; embed %" PRId64 " us mean, %" PRId64
           " us peak after the first, %" PRId64 " us; front end %" PRId64 " us mean\n",
           head.windows, units_off, norms_off, own_norms_off, off, head.prints,
           prints_same ? "same" : "differ", ai::image_speaker_threshold_milli_z(head.prints), passed[kOwner],
           tried[kOwner], theirs[kOwner], passed[kMember], tried[kMember], theirs[kMember], passed[kImpostor],
           tried[kImpostor], t.embed_us / (head.windows - 1), t.embed_peak_us, t.first_us,
           t.front_us / head.windows);
    printf("speaker chain: the first check after load kept %" PRId64 " B internal and %" PRId64 " B PSRAM\n",
           t.first_internal_bytes, t.first_psram_bytes);
    const uint32_t scored = tried[kOwner] + tried[kImpostor] + tried[kMember];
    printf("speaker chain: a voiceprint's norm %" PRId64 " us mean; a window's score over %" PRIu32
           " voiceprints %" PRId64 " us mean, %" PRId64 " us peak\n",
           norm_us / head.prints, head.prints, score_us / scored, score_peak_us);
    heap_caps_free(units);
    heap_caps_free(prints);
    esp_partition_munmap(handle);
    TEST_ASSERT_EQUAL(0, units_off);
    TEST_ASSERT_TRUE(prints_same);
    TEST_ASSERT_EQUAL(0, norms_off);
    TEST_ASSERT_EQUAL(0, own_norms_off);
    TEST_ASSERT_EQUAL(0, off);
    TEST_ASSERT_EQUAL_MESSAGE(0, (int)t.first_internal_bytes, "a check allocates after load (CLAUDE.md 4.1)");
    TEST_ASSERT_EQUAL_MESSAGE(0, (int)t.first_psram_bytes, "a check allocates after load (CLAUDE.md 4.1)");
}

extern "C" void app_main(void)
{
    ESP_ERROR_CHECK(sys_storage_init());
    UNITY_BEGIN();
    unity_run_tests_by_tag("[psram_lines]", false);
    // The whole-graph case erases model slot 0 when it ends, so the cuts run first.
    unity_run_tests_by_tag("[speaker_cuts]", false);
    unity_run_tests_by_tag("[speaker_chain]", false);
    unity_run_tests_by_tag("[speaker]", false);
    UNITY_END();
}
