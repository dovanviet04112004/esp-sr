#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include <map>
#include <string>

#include "ai_engine.h"
#include "detect/detect.hpp"
#include "dl_model_base.hpp"
#include "esp_err.h"
#include "esp_heap_caps.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "storage_format.h"
#include "sys_storage.h"
#include "unity.h"

extern const uint8_t detect_expect_start[] asm("_binary_detect_expect_bin_start");

namespace {

constexpr size_t kNameBytes = 32;
constexpr size_t kOutputsMax = 32;         // the six heads and the taps of probe --taps
constexpr size_t kFields = 6;              // x1 y1 x2 y2 class score_permille
constexpr uint32_t kCrcPoly = 0xEDB88320u; // reflected IEEE polynomial, as zlib.crc32
constexpr size_t kCrcTable = 256;
constexpr size_t kShown = 8;
constexpr int64_t kUsPerMs = 1000;
constexpr uint32_t kInternalCaps = MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT;
constexpr const char *kBroken = "voice"; // test table slot of the flipped image

// Layout written by srpipe.tasks.detect.probe.
struct ExpectHead {
    char magic[4];
    uint32_t pictures;
    uint32_t outputs;
    uint32_t keep; // box rows a picture, zero past its boxes
};
struct OutputRecord {
    char name[kNameBytes]; // the graph's, NUL-ended
    uint32_t elements;
    int32_t exponent;
    uint32_t bytes; // of an element
};
// Then one zlib.crc32 per output, channels last, in the outputs' order.
struct PictureRecord {
    uint32_t width;
    uint32_t height;
    uint32_t offset; // into models_1, RGB888
    uint32_t boxes;
};
static_assert(sizeof(ExpectHead) == 16 && sizeof(OutputRecord) == 44 && sizeof(PictureRecord) == 16,
              "detect_expect.bin layout");

uint32_t s_crc_table[kCrcTable];

struct Heaps {
    size_t internal;
    size_t psram;
};

Heaps free_now()
{
    return {heap_caps_get_free_size(kInternalCaps), heap_caps_get_free_size(MALLOC_CAP_SPIRAM)};
}

Heaps lowest_since_start()
{
    return {heap_caps_get_minimum_free_size(kInternalCaps),
            heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM)};
}

void print_taken(const char *what, const Heaps &from, const Heaps &to)
{
    printf("%s: internal RAM %d B, PSRAM %d B\n", what, (int)from.internal - (int)to.internal,
           (int)from.psram - (int)to.psram);
}

void crc_init()
{
    for (uint32_t i = 0; i < kCrcTable; i++) {
        uint32_t c = i;
        for (int b = 0; b < 8; b++) {
            c = (c >> 1) ^ (kCrcPoly & (0u - (c & 1u)));
        }
        s_crc_table[i] = c;
    }
}

uint32_t crc32_of(const uint8_t *data, size_t n)
{
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < n; i++) {
        crc = (crc >> 8) ^ s_crc_table[(crc ^ data[i]) & 0xFFu];
    }
    return ~crc;
}

dl::TensorBase *output(dl::Model *model, const char *name)
{
    std::map<std::string, dl::TensorBase *> &outputs = model->get_outputs();
    const auto found = outputs.find(name);
    return found != outputs.end() ? found->second : nullptr;
}

int check_outputs(dl::Model *model, const OutputRecord *outs, uint32_t n_outs, const uint32_t *crcs,
                  uint32_t p)
{
    int bad = 0;
    for (uint32_t o = 0; o < n_outs; o++) {
        const OutputRecord &want = outs[o];
        dl::TensorBase *t = output(model, want.name);
        if (t == nullptr || (uint32_t)t->get_size() != want.elements || t->get_exponent() != want.exponent ||
            (uint32_t)t->get_dtype_bytes() != want.bytes) {
            printf("picture %" PRIu32 " %s: not %" PRIu32 " elements of %" PRIu32 " bytes at 2^%" PRId32 "\n",
                   p, want.name, want.elements, want.bytes, want.exponent);
            bad++;
            continue;
        }
        const uint8_t *data = static_cast<const uint8_t *>(t->data);
        const uint32_t crc = crc32_of(data, want.elements * want.bytes);
        if (crc != crcs[o]) {
            printf("picture %" PRIu32 " %s: crc %08" PRIx32 ", ESP-PPQ's %08" PRIx32 "; first values", p,
                   want.name, crc, crcs[o]);
            for (size_t i = 0; i < kShown && i < want.elements; i++) {
                printf(" %d",
                       want.bytes == 1 ? (int)((const int8_t *)data)[i] : (int)((const int16_t *)data)[i]);
            }
            printf("\n");
            bad++;
        }
    }
    return bad;
}

int check_boxes(const ai_engine_box_t *got, size_t n, const int16_t *want, const PictureRecord &pic,
                uint32_t p)
{
    int bad = n == pic.boxes ? 0 : 1;
    if (bad) {
        printf("picture %" PRIu32 ": %u boxes, the mirror's %" PRIu32 "\n", p, (unsigned)n, pic.boxes);
    }
    for (size_t i = 0; i < n && i < pic.boxes; i++) {
        const int16_t *w = want + i * kFields;
        const ai_engine_box_t &g = got[i];
        if (g.x1 != w[0] || g.y1 != w[1] || g.x2 != w[2] || g.y2 != w[3] || g.cls != (uint16_t)w[4] ||
            g.score_permille != (uint16_t)w[5]) {
            printf("picture %" PRIu32 " box %u: %d %d %d %d %u %u, the mirror's %d %d %d %d %d %d\n", p,
                   (unsigned)i, g.x1, g.y1, g.x2, g.y2, g.cls, g.score_permille, w[0], w[1], w[2], w[3], w[4],
                   w[5]);
            bad++;
        }
    }
    return bad;
}

} // namespace

TEST_CASE("detect: the six outputs and the boxes match ESP-PPQ's on eight pictures", "[detect]")
{
    const auto *head = reinterpret_cast<const ExpectHead *>(detect_expect_start);
    TEST_ASSERT_EQUAL_MEMORY("SRDE", head->magic, 4);
    TEST_ASSERT_TRUE(head->outputs <= kOutputsMax && head->keep <= STORAGE_VISION_TOP_K_MAX);
    const auto *outs = reinterpret_cast<const OutputRecord *>(head + 1);
    const auto *records = reinterpret_cast<const uint8_t *>(outs + head->outputs);
    const size_t record_bytes = sizeof(PictureRecord) + head->outputs * sizeof(uint32_t);
    const auto *tables = reinterpret_cast<const int16_t *>(records + head->pictures * record_bytes);
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                                           STORAGE_MODEL_LABEL_SLOT1);
    TEST_ASSERT_NOT_NULL_MESSAGE(part, "no models_1: build with test_apps/partitions_unit.csv");
    const void *base = nullptr;
    esp_partition_mmap_handle_t handle;
    TEST_ASSERT_EQUAL(ESP_OK,
                      esp_partition_mmap(part, 0, part->size, ESP_PARTITION_MMAP_DATA, &base, &handle));
    crc_init();
    const Heaps before = free_now();
    int64_t started = esp_timer_get_time();
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_detect_load(STORAGE_MODEL_LABEL_SLOT0),
                              "models_0 lacks the vision image: make ai-unit-detect");
    printf("loaded in %lld ms\n", (long long)((esp_timer_get_time() - started) / kUsPerMs));
    const Heaps loaded = free_now();
    print_taken("the loaded graph holds", before, loaded);
    dl::Model *model = ai::detect_model();
    static ai_engine_box_t boxes[STORAGE_VISION_TOP_K_MAX];
    int bad = 0;
    long long total_ms = 0;
    long long peak_ms = 0;
    TEST_ASSERT_EQUAL(ESP_OK, heap_caps_monitor_local_minimum_free_size_start());
    for (uint32_t p = 0; p < head->pictures; p++) {
        const auto &pic = *reinterpret_cast<const PictureRecord *>(records + p * record_bytes);
        const auto *crcs =
            reinterpret_cast<const uint32_t *>(records + p * record_bytes + sizeof(PictureRecord));
        const uint8_t *rgb = static_cast<const uint8_t *>(base) + pic.offset;
        size_t n = 0;
        started = esp_timer_get_time();
        TEST_ASSERT_EQUAL(ESP_OK, ai_engine_detect_run(rgb, (uint16_t)pic.width, (uint16_t)pic.height, boxes,
                                                       STORAGE_VISION_TOP_K_MAX, &n));
        const long long ms = (long long)((esp_timer_get_time() - started) / kUsPerMs);
        total_ms += ms;
        peak_ms = ms > peak_ms ? ms : peak_ms;
        bad += check_outputs(model, outs, head->outputs, crcs, p);
        bad += check_boxes(boxes, n, tables + (size_t)p * head->keep * kFields, pic, p);
        printf("picture %" PRIu32 ": %" PRIu32 "x%" PRIu32 " in %lld ms, %u boxes", p, pic.width, pic.height,
               ms, (unsigned)n);
        if (n > 0) {
            printf(", best %s at %u permille", ai_engine_detect_name(boxes[0].cls), boxes[0].score_permille);
        }
        printf("\n");
    }
    const Heaps lowest = lowest_since_start();
    TEST_ASSERT_EQUAL(ESP_OK, heap_caps_monitor_local_minimum_free_size_stop());
    printf("%" PRIu32 " pictures: mean %lld ms, peak %lld ms\n", head->pictures,
           total_ms / (long long)head->pictures, peak_ms);
    print_taken("a run peaked beyond the loaded graph at", loaded, lowest);
    // esp-dl's time a module on the last picture, which make ai-profile ranks (KEHOACH 3.14 step 2.2).
    model->profile_module(true);
    ai_engine_detect_unload();
    print_taken("unload left taken", before, free_now());
    esp_partition_munmap(handle);
    TEST_ASSERT_EQUAL_MESSAGE(0, bad, "the chip and ESP-PPQ differ, see the lines above");
}

TEST_CASE("detect: a load and unload after the first gives back all the memory it takes", "[detect]")
{
    // A model swap reloads without a reboot (E15-T8): only the first load may keep anything.
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_detect_load(STORAGE_MODEL_LABEL_SLOT0));
    ai_engine_detect_unload();
    const Heaps first = free_now();
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_detect_load(STORAGE_MODEL_LABEL_SLOT0));
    ai_engine_detect_unload();
    const Heaps second = free_now();
    print_taken("a load and unload after the first left taken", first, second);
    TEST_ASSERT_EQUAL_MESSAGE(first.internal, second.internal, "internal RAM not given back");
    TEST_ASSERT_EQUAL_MESSAGE(first.psram, second.psram, "PSRAM not given back");
}

TEST_CASE("detect: a vision image whose .espdl differs from its sha256 is refused", "[detect]")
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_CRC, ai_engine_detect_load(kBroken));
}

extern "C" void app_main(void)
{
    ESP_ERROR_CHECK(sys_storage_init());
    UNITY_BEGIN();
    unity_run_tests_by_tag("[detect]", false);
    UNITY_END();
}
