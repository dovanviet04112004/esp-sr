#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "ai_engine.h"
#include "app_events.h"
#include "esp_partition.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gen_grid.h"
#include "gen_listen.h"
#include "storage_format.h"
#include "svc_listen.h"
#include "sys_storage.h"
#include "unity.h"

// Layout written by srpipe.tasks.command.ctc.probe's listen_rounds: a record a partition of the round.
typedef struct __attribute__((packed)) {
    char magic[4];
    uint16_t sessions, commands, reject, margin;
} listen_head_t;
typedef struct __attribute__((packed)) {
    uint32_t hops;
    uint16_t segments, windows;
} session_head_t;
typedef struct __attribute__((packed)) {
    uint32_t first, hops;
} segment_head_t;
typedef struct __attribute__((packed)) {
    uint32_t first, last;
    int16_t command;
    uint16_t score, margin, gap;
} window_t;
_Static_assert(sizeof(listen_head_t) == 12 && sizeof(session_head_t) == 8 && sizeof(segment_head_t) == 8 &&
                   sizeof(window_t) == 16,
               "listen record layout");

#define PERMILLE_MAX 1000  // event.schema's cap of score and margin
#define SEQ_APART 1000000u // sessions apart in seq: each starts afresh

#define WORK_YIELD_HOPS 16 // work hops a yield: IDLE0 feeds the watchdog
#define DECIDED_MAX 64

static const char *const k_labels[] = {STORAGE_MODEL_LABEL_SLOT1, "voice"};

typedef struct {
    const char *ids[AI_ENGINE_COMMANDS_MAX];
    const char *texts[AI_ENGINE_COMMANDS_MAX];
    uint16_t n;
} named_t;

typedef struct {
    size_t windows, differ, decisions;
    int64_t work_us, work_peak_us;
} tally_t;

static const int16_t k_zeros[GEN_GRID_HOP_SAMPLES];

static size_t padded4(size_t n)
{
    return (n + 3) / 4 * 4;
}

// The ids and texts after the head; returns where the sessions start.
static const uint8_t *read_named(const uint8_t *base, uint16_t commands, named_t *out)
{
    const char *at = (const char *)base + sizeof(listen_head_t);
    out->n = commands;
    for (uint16_t c = 0; c < commands; c++) {
        out->ids[c] = at;
        at += strlen(at) + 1;
        out->texts[c] = at;
        at += strlen(at) + 1;
    }
    return base + padded4((size_t)((const uint8_t *)at - base));
}

static size_t drain(svc_listen_decision_t *decided, size_t n, tally_t *t)
{
    size_t hops = 0;
    while (svc_listen_pending()) {
        if (svc_listen_work(&decided[n])) {
            t->work_us += decided[n].work_us;
            t->work_peak_us = decided[n].work_us > t->work_peak_us ? decided[n].work_us : t->work_peak_us;
            TEST_ASSERT_LESS_THAN(DECIDED_MAX, n + 1);
            n++;
        }
        if (++hops % WORK_YIELD_HOPS == 0) { vTaskDelay(1); }
    }
    return n;
}

static bool same(const window_t *want, const svc_listen_decision_t *got, const named_t *named, uint32_t base)
{
    const app_event_t *e = &got->event;
    const bool command = want->command >= 0 ? e->kind == APP_EVT_COMMAND &&
                                                  strcmp(e->command_id, named->ids[want->command]) == 0
                                            : e->kind == APP_EVT_REJECT;
    const uint16_t score = want->score < PERMILLE_MAX ? want->score : PERMILLE_MAX;
    const uint16_t margin = want->margin < PERMILLE_MAX ? want->margin : PERMILLE_MAX;
    return command && e->score_permille == score && e->margin_permille == margin &&
           got->free_gap_permille == want->gap && got->first_seq == base + want->first &&
           e->seq == base + want->last;
}

// One session hop by hop through svc_listen, zeros where no window reads, then a gap of silence that closes
// its last utterance; every window decided compared with Python's. Returns where the next session starts.
static const uint8_t *run_session(const uint8_t *at, uint32_t base, const named_t *named, tally_t *t)
{
    session_head_t h;
    memcpy(&h, at, sizeof(h));
    const uint8_t *vad = at + sizeof(h);
    const uint8_t *p = vad + padded4((h.hops + 7) / 8);
    const uint8_t *segments = p;
    for (uint16_t s = 0; s < h.segments; s++) {
        segment_head_t seg;
        memcpy(&seg, p, sizeof(seg));
        p += sizeof(seg) + seg.hops * GEN_GRID_HOP_SAMPLES * sizeof(int16_t);
    }
    const window_t *want = (const window_t *)p;
    static svc_listen_decision_t decided[DECIDED_MAX];
    size_t n = 0;
    const uint8_t *seg_at = segments;
    segment_head_t seg = {0};
    uint16_t seg_index = 0;
    if (h.segments > 0) { memcpy(&seg, seg_at, sizeof(seg)); }
    for (uint32_t hop = 0; hop < h.hops + GEN_LISTEN_UTTERANCE_GAP_HOPS + 1; hop++) {
        while (seg_index < h.segments && hop >= seg.first + seg.hops) {
            seg_at += sizeof(seg) + seg.hops * GEN_GRID_HOP_SAMPLES * sizeof(int16_t);
            if (++seg_index < h.segments) { memcpy(&seg, seg_at, sizeof(seg)); }
        }
        const int16_t *pcm = k_zeros;
        if (seg_index < h.segments && hop >= seg.first) {
            pcm = (const int16_t *)(seg_at + sizeof(seg)) + (hop - seg.first) * GEN_GRID_HOP_SAMPLES;
        }
        const bool on = hop < h.hops && (vad[hop / 8] >> (hop % 8)) & 1;
        TEST_ASSERT_EQUAL(ESP_OK, svc_listen_feed(pcm, base + hop, on));
        n = drain(decided, n, t);
    }
    TEST_ASSERT_EQUAL_MESSAGE(h.windows, n, "svc_listen cut otherwise than Gate 3");
    for (size_t w = 0; w < n; w++) {
        const bool ok = same(&want[w], &decided[w], named, base);
        t->differ += ok ? 0 : 1;
        const app_event_t *e = &decided[w].event;
        printf("listen window %" PRIu32 "..%" PRIu32 ": board %s %s %u %u %u, python %d %u %u %u%s\n",
               decided[w].first_seq - base, e->seq - base, e->kind == APP_EVT_COMMAND ? "command" : "reject",
               e->kind == APP_EVT_COMMAND ? e->command_id : e->code, e->score_permille, e->margin_permille,
               decided[w].free_gap_permille, want[w].command, want[w].score, want[w].margin, want[w].gap,
               ok ? "" : " DIFFERS");
    }
    t->windows += n;
    return p + h.windows * sizeof(window_t);
}

TEST_CASE("svc_listen decides every Gate 3 session of the round as Python decides it, field by field",
          "[svc_listen]")
{
    const esp_partition_t *first =
        esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, k_labels[0]);
    TEST_ASSERT_NOT_NULL(first);
    const void *mapped = NULL;
    esp_partition_mmap_handle_t handle;
    TEST_ASSERT_EQUAL(ESP_OK,
                      esp_partition_mmap(first, 0, first->size, ESP_PARTITION_MMAP_DATA, &mapped, &handle));
    listen_head_t head;
    memcpy(&head, mapped, sizeof(head));
    TEST_ASSERT_EQUAL_MEMORY_MESSAGE("SRLS", head.magic, 4, "no listen record: make listen-unit");
    named_t named;
    read_named(mapped, head.commands, &named);

    const esp_err_t init = sys_storage_init();
    TEST_ASSERT_TRUE(init == ESP_OK || init == ESP_ERR_INVALID_STATE);
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_REJECT, head.reject));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN, head.margin));
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(0),
                              "models_0 lacks the locked models: make listen-unit");
    TEST_ASSERT_TRUE(ai_engine_has(AI_ENGINE_MODEL_COMMAND));
    const svc_listen_config_t cfg = {
        .commands = {.texts = named.texts, .ids = named.ids, .n_commands = (uint8_t)named.n, .version = 1},
        .dialects = LANG_VI_DIALECT_ALL,
        .reject_permille = head.reject,
        .margin_permille = head.margin,
    };
    TEST_ASSERT_EQUAL(ESP_OK, svc_listen_init(&cfg));
    esp_partition_munmap(handle);

    tally_t t = {0};
    uint32_t base = 0;
    size_t sessions = 0;
    for (size_t k = 0; k < sizeof(k_labels) / sizeof(k_labels[0]); k++) {
        const esp_partition_t *part =
            esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, k_labels[k]);
        TEST_ASSERT_NOT_NULL(part);
        TEST_ASSERT_EQUAL(ESP_OK,
                          esp_partition_mmap(part, 0, part->size, ESP_PARTITION_MMAP_DATA, &mapped, &handle));
        listen_head_t h;
        memcpy(&h, mapped, sizeof(h));
        TEST_ASSERT_EQUAL_MEMORY(head.magic, h.magic, 4);
        const uint8_t *at = read_named(mapped, h.commands, &named);
        for (uint16_t s = 0; s < h.sessions; s++) {
            at = run_session(at, base, &named, &t);
            base += SEQ_APART;
            sessions++;
        }
        esp_partition_munmap(handle);
    }
    printf("svc_listen: %u sessions, %u windows, %u decided otherwise than python; work %" PRId64
           " us mean, %" PRId64 " us peak a window\n",
           (unsigned)sessions, (unsigned)t.windows, (unsigned)t.differ,
           t.windows ? t.work_us / (int64_t)t.windows : 0, t.work_peak_us);
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_KWS, STORAGE_KEY_CMD_REJECT));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN));
    TEST_ASSERT_EQUAL(0, t.differ);
}

void app_main(void)
{
    UNITY_BEGIN();
    unity_run_all_tests();
    UNITY_END();
}
