#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include <atomic>

#include "ai_engine.h"
#include "dsp_spec/swift_front.h"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gen_listen.h"
#include "gen_speaker.h"
#include "sys_storage.h"
#include "unity.h"

namespace {

constexpr int kWindows = 2;
constexpr int kHops = 128; // a command window; the wake's hops and the pitch's frames
constexpr int kFrontValues = DSP_SPEC_SWIFT_FRONT_SPECTRA * DSP_SPEC_SWIFT_FRONT_BANDS;
constexpr int kCommands = 3;
constexpr size_t kDimsMax = 256;
constexpr uint32_t kTaskStackBytes = 8192;
constexpr BaseType_t kSachCore = 1;
constexpr BaseType_t kListenCore = 0;
constexpr UBaseType_t kSachPriority = 16; // as on the product (KEHOACH 5.2)
constexpr UBaseType_t kXacPriority = 3;   // xac_task's over nhan_task's window work
constexpr UBaseType_t kWorkPriority = 2;  // nhan_task's window work
constexpr UBaseType_t kEmbedPriority = 1; // a speaker run in core 0's idle time
constexpr size_t kFrameBytes = 530;       // a dsp_afe_frame_t into q_clean's PSRAM storage
constexpr size_t kRingFrames = 128;       // q_clean's depth
constexpr uint32_t kFrameMs = 16;         // a hop
constexpr int64_t kFramesRunUs = 60 * 1000 * 1000;
constexpr int kStepsBetweenRests = 16; // a command chunk
constexpr int kInterleaveRuns = 12;
constexpr uint32_t kSpeakerRestMs = 150; // between two checks, as commands come apart
constexpr uint32_t kIdleRestMs = 20;     // core 0 idle while the window work waits for pitch
constexpr int64_t kUsPerMs = 1000;

// Two syllables, one, two: segment ids under the tones, each syllable ending on a tone (gen_units.h).
constexpr uint8_t kReadings[kCommands][6] = {{5, 20, 38, 9, 22, 40}, {12, 25, 41}, {3, 18, 39, 7, 30, 42}};
constexpr uint8_t kReadingUnits[kCommands] = {6, 3, 6};

struct Inputs {
    int16_t *pcm;
    float *front;
    float *mel;
    float *features;
    size_t dims;
    size_t feature_count;
    size_t pitch_frames;
    ai_engine_lexicon_t *lexicon;
};

struct Outputs {
    float log_f0[kHops];
    float confidence[kHops];
    uint16_t wake[kHops];
    ai_engine_command_result_t command;
    float units[kWindows][kDimsMax];
};

struct Sach {
    uint8_t *ring;
    std::atomic<uint32_t> written{0};
    std::atomic<bool> stop{false};
    std::atomic<bool> stopped{false};
};

struct Speaker {
    std::atomic<bool> stop{false};
    std::atomic<bool> stopped{false};
    int runs = 0;
    int differ = 0;
    float unit[kDimsMax];
};

uint32_t next(uint32_t *state)
{
    *state = *state * 1664525u + 1013904223u;
    return *state >> 8;
}

float uniform(uint32_t *state, float low, float high)
{
    return low + (high - low) * static_cast<float>(next(state) & 0xFFFF) / 65535.0f;
}

void *take(size_t bytes)
{
    void *p = heap_caps_calloc(1, bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    TEST_ASSERT_NOT_NULL(p);
    return p;
}

const Inputs &inputs()
{
    static Inputs in = {};
    if (in.pcm != nullptr) { return in; }
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(0),
                              "models_0 lacks the product's models.bin: make ai-unit-cores");
    in.dims = ai_engine_speaker_dims();
    in.feature_count = ai_engine_command_features();
    in.pitch_frames = ai_engine_pitch_frames();
    TEST_ASSERT_TRUE(in.dims > 0 && in.dims <= kDimsMax && in.pitch_frames > 0);
    TEST_ASSERT_TRUE(in.feature_count >= GEN_LISTEN_N_BANDS && kHops % in.pitch_frames == 0);
    in.pcm = static_cast<int16_t *>(take(sizeof(int16_t) * kWindows * GEN_SPEAKER_WINDOW_SAMPLES));
    in.front = static_cast<float *>(take(sizeof(float) * kHops * kFrontValues));
    in.mel = static_cast<float *>(take(sizeof(float) * kHops * GEN_LISTEN_N_BANDS));
    in.features = static_cast<float *>(take(sizeof(float) * kHops * in.feature_count));
    in.lexicon = static_cast<ai_engine_lexicon_t *>(take(sizeof(ai_engine_lexicon_t)));
    uint32_t state = 20261029;
    for (size_t i = 0; i < kWindows * GEN_SPEAKER_WINDOW_SAMPLES; i++) {
        in.pcm[i] = static_cast<int16_t>(static_cast<int32_t>(next(&state) & 0x1FFF) - 0x1000);
    }
    for (size_t i = 0; i < kHops * kFrontValues; i++) {
        in.front[i] = uniform(&state, -8.0f, 0.0f);
    }
    for (size_t i = 0; i < kHops * GEN_LISTEN_N_BANDS; i++) {
        in.mel[i] = uniform(&state, -12.0f, -2.0f);
    }
    for (size_t h = 0; h < kHops; h++) {
        for (size_t f = 0; f < in.feature_count; f++) {
            const bool mel = f < GEN_LISTEN_N_BANDS;
            in.features[h * in.feature_count + f] =
                mel ? uniform(&state, -12.0f, -2.0f) : uniform(&state, 0.0f, 1.0f);
        }
    }
    in.lexicon->n_commands = kCommands;
    for (int c = 0; c < kCommands; c++) {
        in.lexicon->n_variants[c] = 1;
        in.lexicon->variants[c][0] = {kReadingUnits[c], kReadings[c]};
    }
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_prepare(in.lexicon));
    return in;
}

// rest: ticks asleep between chunks, as nhan_task waits for frames, so a lower task runs mid-graph.
void run_listen(const Inputs &in, Outputs *out, TickType_t rest)
{
    ai_engine_pitch_reset();
    for (size_t at = 0; at < kHops; at += in.pitch_frames) {
        TEST_ASSERT_EQUAL(ESP_OK, ai_engine_pitch_run(in.front + at * kFrontValues, out->log_f0 + at,
                                                      out->confidence + at));
        if (rest > 0) { vTaskDelay(rest); }
    }
    ai_engine_wake_reset();
    for (int h = 0; h < kHops; h++) {
        TEST_ASSERT_EQUAL(ESP_OK, ai_engine_wake_step(in.mel + h * GEN_LISTEN_N_BANDS, &out->wake[h]));
    }
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_begin());
    for (int h = 0; h < kHops; h++) {
        TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_step(in.features + h * in.feature_count));
        if (rest > 0 && (h + 1) % kStepsBetweenRests == 0) { vTaskDelay(rest); }
    }
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_command_score(in.lexicon, &out->command));
}

void run_all(const Inputs &in, Outputs *out)
{
    memset(out, 0, sizeof(*out));
    run_listen(in, out, 0);
    for (int w = 0; w < kWindows; w++) {
        TEST_ASSERT_EQUAL(ESP_OK,
                          ai_engine_speaker_embed(in.pcm + w * GEN_SPEAKER_WINDOW_SAMPLES, out->units[w]));
    }
}

const Outputs &alone()
{
    static Outputs *ref = nullptr;
    if (ref != nullptr) { return *ref; }
    ref = static_cast<Outputs *>(take(sizeof(Outputs)));
    const Inputs &in = inputs();
    const int64_t from_us = esp_timer_get_time();
    run_all(in, ref);
    printf("cores: the four graphs alone %" PRId64 " ms\n", (esp_timer_get_time() - from_us) / kUsPerMs);
    return *ref;
}

void sach_loop(void *arg)
{
    auto *s = static_cast<Sach *>(arg);
    uint32_t i = 0;
    while (!s->stop.load()) {
        // Each frame is its own number in every byte, so core 0 can tell a lost write.
        memset(s->ring + (i % kRingFrames) * kFrameBytes, static_cast<uint8_t>(i), kFrameBytes);
        s->written.store(++i, std::memory_order_release);
        vTaskDelay(pdMS_TO_TICKS(kFrameMs));
    }
    s->stopped.store(true);
    vTaskDelete(nullptr);
}

// Frames core 1 wrote since the last call, read back as nhan_task reads q_clean.
uint32_t check_frames(const Sach &s, uint32_t *read)
{
    uint32_t wrong = 0;
    const uint32_t written = s.written.load(std::memory_order_acquire);
    for (; *read < written; (*read)++) {
        if (written - *read >= kRingFrames) { continue; }
        const uint8_t *frame = s.ring + (*read % kRingFrames) * kFrameBytes;
        for (size_t b = 0; b < kFrameBytes; b++) {
            wrong += frame[b] != static_cast<uint8_t>(*read);
        }
    }
    return wrong;
}

void speaker_loop(void *arg)
{
    auto *k = static_cast<Speaker *>(arg);
    const Inputs &in = inputs();
    const Outputs &ref = alone();
    while (!k->stop.load()) {
        for (int w = 0; w < kWindows && !k->stop.load(); w++) {
            k->differ +=
                ai_engine_speaker_embed(in.pcm + w * GEN_SPEAKER_WINDOW_SAMPLES, k->unit) != ESP_OK ||
                memcmp(k->unit, ref.units[w], in.dims * sizeof(float)) != 0;
            k->runs++;
            vTaskDelay(pdMS_TO_TICKS(kSpeakerRestMs));
        }
    }
    k->stopped.store(true);
    vTaskDelete(nullptr);
}

// The speaker's graph in its own task beside this one's pitch, wake and command, preempting each other
// mid-graph on core 0 as xac_task and nhan_task do.
void run_interleaved(UBaseType_t speaker_priority, UBaseType_t listen_priority, TickType_t rest)
{
    const Outputs &ref = alone();
    static Speaker k;
    k.stop.store(false);
    k.stopped.store(false);
    k.runs = 0;
    k.differ = 0;
    const UBaseType_t own = uxTaskPriorityGet(nullptr);
    vTaskPrioritySet(nullptr, listen_priority);
    TEST_ASSERT_EQUAL(pdPASS, xTaskCreatePinnedToCore(speaker_loop, "speaker", kTaskStackBytes, &k,
                                                      speaker_priority, nullptr, kListenCore));
    auto *out = static_cast<Outputs *>(take(sizeof(Outputs)));
    int runs = 0;
    int differ = 0;
    const int64_t from_us = esp_timer_get_time();
    while (runs < kInterleaveRuns) {
        memcpy(out, &ref, sizeof(*out));
        run_listen(inputs(), out, rest);
        runs++;
        differ += memcmp(out, &ref, sizeof(*out)) != 0;
    }
    const int64_t ms_a_run = (esp_timer_get_time() - from_us) / runs / kUsPerMs;
    k.stop.store(true);
    while (!k.stopped.load()) {
        vTaskDelay(1);
    }
    vTaskPrioritySet(nullptr, own);
    printf("cores: speaker at %u, pitch, wake and command at %u, both on core 0: %d runs, %d differ, %" PRId64
           " ms a run; %d speaker runs, %d differ\n",
           static_cast<unsigned>(speaker_priority), static_cast<unsigned>(listen_priority), runs, differ,
           ms_a_run, k.runs, k.differ);
    heap_caps_free(out);
    TEST_ASSERT_EQUAL(0, differ);
    TEST_ASSERT_EQUAL(0, k.differ);
}

} // namespace

TEST_CASE("core 0's four graphs give their bits alone while core 1 writes q_clean's frames as sach does",
          "[ai_engine][cores_frames]")
{
    const Outputs &ref = alone();
    static Sach s;
    s.ring = static_cast<uint8_t *>(take(kRingFrames * kFrameBytes));
    TEST_ASSERT_EQUAL(pdPASS, xTaskCreatePinnedToCore(sach_loop, "sach", kTaskStackBytes, &s, kSachPriority,
                                                      nullptr, kSachCore));
    auto *out = static_cast<Outputs *>(take(sizeof(Outputs)));
    int runs = 0;
    int differ = 0;
    uint32_t read = 0;
    uint32_t frame_bytes_wrong = 0;
    const int64_t from_us = esp_timer_get_time();
    while (esp_timer_get_time() - from_us < kFramesRunUs) {
        run_all(inputs(), out);
        runs++;
        differ += memcmp(out, &ref, sizeof(*out)) != 0;
        frame_bytes_wrong += check_frames(s, &read);
        vTaskDelay(1);
    }
    const int64_t ms_a_run = (esp_timer_get_time() - from_us) / runs / kUsPerMs;
    s.stop.store(true);
    while (!s.stopped.load()) {
        vTaskDelay(1);
    }
    printf("cores: sach's frames on core 1, the four graphs on core 0: %d runs, %d differ, %" PRId64
           " ms a run; %" PRIu32 " frames, %" PRIu32 " frame bytes wrong\n",
           runs, differ, ms_a_run, s.written.load(), frame_bytes_wrong);
    heap_caps_free(out);
    TEST_ASSERT_EQUAL(0, frame_bytes_wrong);
    TEST_ASSERT_EQUAL(0, differ);
}

TEST_CASE(
    "the speaker over pitch, wake and command on core 0 gives each graph's bits, as xac_task over nhan_task",
    "[ai_engine][cores_over]")
{
    run_interleaved(kXacPriority, kWorkPriority, 1);
}

TEST_CASE(
    "pitch, wake and command over the speaker on core 0 give each graph's bits, the speaker in idle time",
    "[ai_engine][cores_under]")
{
    run_interleaved(kEmbedPriority, kWorkPriority, pdMS_TO_TICKS(kIdleRestMs));
}

extern "C" void app_main(void)
{
    ESP_ERROR_CHECK(sys_storage_init());
    UNITY_BEGIN();
    unity_run_tests_by_tag("[cores_under]", false);
    unity_run_tests_by_tag("[cores_over]", false);
    unity_run_tests_by_tag("[cores_frames]", false);
    UNITY_END();
}
