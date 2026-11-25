#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gold_read.h"
#include "ns_gtcrn/gtcrn.h"
#include "unity.h"

#define BINS AI_ENGINE_GTCRN_BINS
#define TIMED_HOPS 200
#define WARM_HOPS 20

extern const uint8_t case_start[] asm("_binary_case_000_gold_start");
extern const uint8_t case_end[] asm("_binary_case_000_gold_end");

typedef struct {
    uint8_t *buf;
    gold_tensor_t weights, bins, gain;
    ai_engine_gtcrn_weights_t w;
} golden_t;

static bool find(const golden_t *g, const char *name, gold_tensor_t *out)
{
    gold_reader_t r;
    if (!gold_open(&r, g->buf, (size_t)(case_end - case_start))) { return false; }
    while (gold_next(&r, out)) {
        if (strcmp(out->name, name) == 0) { return true; }
    }
    return false;
}

// The embedded case is copied to the heap: its tensors need the 4-byte alignment of a malloc block.
static golden_t open_golden(void)
{
    golden_t g = {.buf = heap_caps_malloc((size_t)(case_end - case_start), MALLOC_CAP_SPIRAM)};
    TEST_ASSERT_NOT_NULL(g.buf);
    memcpy(g.buf, case_start, (size_t)(case_end - case_start));
    TEST_ASSERT_TRUE(find(&g, "weights", &g.weights) && find(&g, "bins", &g.bins) &&
                     find(&g, "gain", &g.gain));
    TEST_ASSERT_EQUAL(ESP_OK, ai_engine_gtcrn_weights(g.weights.data, g.weights.dims[0], &g.w));
    return g;
}

TEST_CASE("gtcrn on the chip gives the mirror's gains bit for bit", "[gtcrn]")
{
    golden_t g = open_golden();
    ai_engine_gtcrn_state_t *st = heap_caps_malloc(sizeof(*st), MALLOC_CAP_SPIRAM);
    TEST_ASSERT_NOT_NULL(st);
    ai_engine_gtcrn_reset(st);
    const float *bins = g.bins.data;
    const float *want = g.gain.data;
    float got[BINS];
    size_t differ = 0;
    for (size_t h = 0; h < g.bins.dims[0]; h++) {
        ai_engine_gtcrn_step(&g.w, st, bins + h * 2 * BINS, got);
        differ += memcmp(got, want + h * BINS, sizeof(got)) != 0;
    }
    printf("gtcrn golden: %u of %u hops differ\n", (unsigned)differ, (unsigned)g.bins.dims[0]);
    TEST_ASSERT_EQUAL(0, differ);
    heap_caps_free(st);
    heap_caps_free(g.buf);
}

static void time_hops(const golden_t *g, uint32_t caps, const char *where)
{
    ai_engine_gtcrn_state_t *st = heap_caps_malloc(sizeof(*st), caps);
    TEST_ASSERT_NOT_NULL(st);
    ai_engine_gtcrn_reset(st);
    const float *bins = g->bins.data;
    float gain[BINS];
    int64_t total = 0;
    int64_t worst = 0;
    for (int h = 0; h < WARM_HOPS + TIMED_HOPS; h++) {
        const int64_t t0 = esp_timer_get_time();
        ai_engine_gtcrn_step(&g->w, st, bins + (size_t)(h % g->bins.dims[0]) * 2 * BINS, gain);
        const int64_t dt = esp_timer_get_time() - t0;
        if (h >= WARM_HOPS) {
            total += dt;
            worst = dt > worst ? dt : worst;
        }
        vTaskDelay(1);
    }
    printf("gtcrn hop, state in %s (%u bytes): mean %" PRId64 " us, worst %" PRId64 " us\n", where,
           (unsigned)sizeof(*st), total / TIMED_HOPS, worst);
    heap_caps_free(st);
}

TEST_CASE("gtcrn hop time, weights in PSRAM, state in internal RAM then PSRAM", "[gtcrn]")
{
    golden_t g = open_golden();
    time_hops(&g, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT, "internal RAM");
    time_hops(&g, MALLOC_CAP_SPIRAM, "PSRAM");
    heap_caps_free(g.buf);
}

void app_main(void)
{
    UNITY_BEGIN();
    unity_run_tests_by_tag("[gtcrn]", false);
    UNITY_END();
}
