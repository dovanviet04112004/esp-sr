#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "ai_engine.h"
#include "app_err.h"
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
    uint16_t speaker; // LISTEN_SPEAKER_* flags
    uint16_t pad;
    char owner[APP_SPEAKER_ID_BYTES]; // the roster's one person, enrolled ahead of a session
} listen_head_t;
typedef struct __attribute__((packed)) {
    uint32_t hops;
    uint16_t segments, windows;
    uint16_t enrol; // spk enrol ahead of this session
    uint16_t pad;
} session_head_t;
typedef struct __attribute__((packed)) {
    uint32_t first, hops;
} segment_head_t;
typedef struct __attribute__((packed)) {
    uint32_t first, last;
    int16_t command;
    uint16_t score, margin, gap, syllable;
    int16_t speaker;      // the check's AS-norm milli-z, or commands enrolled
    int16_t cosine;       // of the voiceprint giving that score
    uint8_t speaker_code; // CHECK_*
    uint8_t noise_bin;    // of a scored check, ADR-0024
} window_t;
_Static_assert(sizeof(listen_head_t) == 24 && sizeof(session_head_t) == 12 && sizeof(segment_head_t) == 8 &&
                   sizeof(window_t) == 24,
               "listen record layout");

enum { LISTEN_SPEAKER_ON = 1, LISTEN_SPEAKER_AFRESH = 2 };
enum { CHECK_NONE = 0, CHECK_PASSED, CHECK_STOPPED, CHECK_UNENROLLED, CHECK_ENROLLING };

#define PERMILLE_MAX 1000  // event.schema's cap of score and margin
#define SEQ_APART 1000000u // sessions apart in seq: each starts afresh

#define WORK_YIELD_HOPS 16 // work hops a yield: IDLE0 feeds the watchdog
#define DECIDED_MAX 64
#define CLICK_HOPS 2          // vad hops of a click, too short to be an utterance
#define CLICK_SEQ 0xC0000000u // past every round's sessions, so svc_listen starts afresh
#define RESET_SEQ (CLICK_SEQ + SEQ_APART)
#define CLOSED_SEQ (RESET_SEQ + SEQ_APART)

#define VERIFY_SLOT 0 // one task stands for nhan_task and xac_task here
#define OWNER_NAME "owner"

_Static_assert(CLICK_HOPS < GEN_LISTEN_UTTERANCE_MIN_HOPS, "a click is dropped, never decided");

static const char *const k_labels[] = {STORAGE_MODEL_LABEL_SLOT1, "voice"};

typedef struct {
    const char *ids[AI_ENGINE_COMMANDS_MAX];
    const char *texts[AI_ENGINE_COMMANDS_MAX];
    uint16_t n;
} named_t;

typedef struct {
    size_t windows, differ, decisions, held;
    int64_t work_us, work_peak_us, close_us, close_peak_us, hold_us, hold_peak_us;
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

// A COMMAND goes through the speaker check as nhan_task hands it to xac_task, when the check is on.
static void check_speaker(svc_listen_decision_t *d, tally_t *t)
{
    if (d->event.kind != APP_EVT_COMMAND || !svc_listen_verifies()) { return; }
    const int64_t from_us = esp_timer_get_time();
    TEST_ASSERT_EQUAL(ESP_OK, svc_listen_hold(d, VERIFY_SLOT));
    const int64_t took_us = esp_timer_get_time() - from_us;
    t->held++;
    t->hold_us += took_us;
    t->hold_peak_us = took_us > t->hold_peak_us ? took_us : t->hold_peak_us;
    TEST_ASSERT_EQUAL(ESP_OK, svc_listen_verify(VERIFY_SLOT, d));
}

static size_t drain(svc_listen_decision_t *decided, size_t n, tally_t *t)
{
    size_t hops = 0;
    while (svc_listen_pending()) {
        if (svc_listen_work(&decided[n])) {
            check_speaker(&decided[n], t);
            t->work_us += decided[n].work_us;
            t->work_peak_us = decided[n].work_us > t->work_peak_us ? decided[n].work_us : t->work_peak_us;
            t->close_us += decided[n].close_us;
            t->close_peak_us =
                decided[n].close_us > t->close_peak_us ? decided[n].close_us : t->close_peak_us;
            TEST_ASSERT_LESS_THAN(DECIDED_MAX, n + 1);
            n++;
        }
        if (++hops % WORK_YIELD_HOPS == 0) { vTaskDelay(1); }
    }
    return n;
}

// The speaker check's part: none, the owner's score and cosine, the enrolment's count, or a code of the block
// alone.
static bool same_speaker(const window_t *want, const svc_listen_decision_t *got, const char *owner)
{
    const app_event_t *e = &got->event;
    const bool theirs = strcmp(e->speaker_id, owner) == 0;
    const bool scored = e->has_speaker && got->speaker_milli_z == want->speaker &&
                        e->speaker_permille == want->cosine && got->noise_bin == want->noise_bin;
    switch (want->speaker_code) {
    case CHECK_NONE: return !e->has_speaker && e->speaker_id[0] == '\0' && e->enrol_count == 0;
    case CHECK_PASSED: return e->kind == APP_EVT_COMMAND && scored && theirs;
    case CHECK_STOPPED:
        return e->kind == APP_EVT_REJECT && strcmp(e->code, APP_CODE_SPEAKER) == 0 && scored && theirs;
    case CHECK_ENROLLING:
        return e->kind == APP_EVT_REJECT && strcmp(e->code, APP_CODE_SPEAKER_ENROLLING) == 0 &&
               !e->has_speaker && e->enrol_count == want->speaker && theirs;
    default:
        return e->kind == APP_EVT_REJECT && strcmp(e->code, APP_CODE_SPEAKER_UNENROLLED) == 0 &&
               !e->has_speaker && e->speaker_id[0] == '\0';
    }
}

static bool same(const window_t *want, const svc_listen_decision_t *got, const named_t *named, uint32_t base,
                 const char *owner)
{
    const app_event_t *e = &got->event;
    const bool checked = want->speaker_code != CHECK_NONE && want->speaker_code != CHECK_PASSED;
    const bool command = checked              ? strcmp(e->command_id, named->ids[want->command]) == 0
                         : want->command >= 0 ? e->kind == APP_EVT_COMMAND &&
                                                    strcmp(e->command_id, named->ids[want->command]) == 0
                                              : e->kind == APP_EVT_REJECT;
    const uint16_t score = want->score < PERMILLE_MAX ? want->score : PERMILLE_MAX;
    const uint16_t margin = want->margin < PERMILLE_MAX ? want->margin : PERMILLE_MAX;
    return command && same_speaker(want, got, owner) && e->score_permille == score &&
           e->margin_permille == margin && got->free_gap_permille == want->gap &&
           got->syllable_gap_permille == want->syllable && got->first_seq == base + want->first &&
           e->seq == base + want->last;
}

// One session and the silence after it, hop by hop through svc_listen, zeros where no window reads, each
// window decided compared with Python's. Returns where the next session starts.
static const uint8_t *run_session(const uint8_t *at, uint32_t base, const named_t *named, const char *owner,
                                  tally_t *t)
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
    if (h.enrol) { TEST_ASSERT_EQUAL(ESP_OK, svc_listen_speaker_enrol(owner)); }
    static svc_listen_decision_t decided[DECIDED_MAX];
    size_t n = 0;
    const uint8_t *seg_at = segments;
    segment_head_t seg = {0};
    uint16_t seg_index = 0;
    if (h.segments > 0) { memcpy(&seg, seg_at, sizeof(seg)); }
    for (uint32_t hop = 0; hop < h.hops; hop++) {
        while (seg_index < h.segments && hop >= seg.first + seg.hops) {
            seg_at += sizeof(seg) + seg.hops * GEN_GRID_HOP_SAMPLES * sizeof(int16_t);
            if (++seg_index < h.segments) { memcpy(&seg, seg_at, sizeof(seg)); }
        }
        const int16_t *pcm = k_zeros;
        if (seg_index < h.segments && hop >= seg.first) {
            pcm = (const int16_t *)(seg_at + sizeof(seg)) + (hop - seg.first) * GEN_GRID_HOP_SAMPLES;
        }
        const bool on = (vad[hop / 8] >> (hop % 8)) & 1;
        TEST_ASSERT_EQUAL(ESP_OK, svc_listen_feed(pcm, base + hop, on, false));
        n = drain(decided, n, t);
    }
    TEST_ASSERT_EQUAL_MESSAGE(h.windows, n, "svc_listen cut otherwise than Gate 3");
    for (size_t w = 0; w < n; w++) {
        const bool ok = same(&want[w], &decided[w], named, base, owner);
        t->differ += ok ? 0 : 1;
        const app_event_t *e = &decided[w].event;
        printf("listen window %" PRIu32 "..%" PRIu32
               ": board %s %s %u %u %u %u speaker %d %d %s %u noise %u, "
               "python %d %u %u %u %u speaker %d %d/%u noise %u%s; %" PRIu32 " us after the close\n",
               decided[w].first_seq - base, e->seq - base, e->kind == APP_EVT_COMMAND ? "command" : "reject",
               e->kind == APP_EVT_COMMAND ? e->command_id : e->code, e->score_permille, e->margin_permille,
               decided[w].free_gap_permille, decided[w].syllable_gap_permille,
               e->has_speaker ? decided[w].speaker_milli_z : 0, e->has_speaker ? e->speaker_permille : 0,
               e->speaker_id[0] != '\0' ? e->speaker_id : "-", e->enrol_count, decided[w].noise_bin,
               want[w].command, want[w].score, want[w].margin, want[w].gap, want[w].syllable, want[w].speaker,
               want[w].cosine, want[w].speaker_code, want[w].noise_bin, ok ? "" : " DIFFERS",
               decided[w].close_us);
    }
    t->windows += n;
    return p + h.windows * sizeof(window_t);
}

// The round's head and set from its first record, mapped until the caller unmaps handle.
static void read_round(listen_head_t *head, named_t *named, esp_partition_mmap_handle_t *handle)
{
    const esp_partition_t *first =
        esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, k_labels[0]);
    TEST_ASSERT_NOT_NULL(first);
    const void *mapped = NULL;
    TEST_ASSERT_EQUAL(ESP_OK,
                      esp_partition_mmap(first, 0, first->size, ESP_PARTITION_MMAP_DATA, &mapped, handle));
    memcpy(head, mapped, sizeof(*head));
    TEST_ASSERT_EQUAL_MEMORY_MESSAGE("SRLS", head->magic, 4, "no listen record: make listen-unit");
    read_named(mapped, head->commands, named);
}

// svc_listen takes one init a boot: whichever case comes first starts it on the round's set and thresholds.
static void listen_on(const listen_head_t *head, const named_t *named)
{
    static bool listening;
    if (listening) { return; }
    const esp_err_t init = sys_storage_init();
    TEST_ASSERT_TRUE(init == ESP_OK || init == ESP_ERR_INVALID_STATE);
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_REJECT, head->reject));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN, head->margin));
    // The rounds are decided uncapped, as probe.py's listen_rounds decide them.
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_SYLLABLE, UINT16_MAX));
    TEST_ASSERT_EQUAL_MESSAGE(ESP_OK, ai_engine_load(0),
                              "models_0 lacks the locked models: make listen-unit");
    TEST_ASSERT_TRUE(ai_engine_has(AI_ENGINE_MODEL_COMMAND));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_KWS, STORAGE_KEY_CMD_REJECT));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_KWS, STORAGE_KEY_CMD_SYLLABLE));
    const svc_listen_config_t cfg = {
        .commands = {.texts = named->texts,
                     .ids = named->ids,
                     .n_commands = (uint16_t)named->n,
                     .version = 1},
        .dialects = LANG_VI_DIALECT_ALL,
        .reject_permille = head->reject,
        .margin_permille = head->margin,
        .syllable_permille = UINT16_MAX,
        .enrol_window_s = CONFIG_SVC_LISTEN_ENROL_WINDOW_S,
    };
    TEST_ASSERT_EQUAL(ESP_OK, svc_listen_init(&cfg));
    listening = true;
}

TEST_CASE("svc_listen takes a new set after a click it began working and dropped", "[svc_listen]")
{
    listen_head_t head;
    named_t named;
    esp_partition_mmap_handle_t handle;
    read_round(&head, &named, &handle);
    listen_on(&head, &named);
    static int16_t click[GEN_GRID_HOP_SAMPLES];
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        click[i] = (int16_t)(i % 2 ? 4000 : -4000);
    }
    static svc_listen_decision_t decided[DECIDED_MAX];
    tally_t t = {0};
    size_t n = 0;
    for (uint32_t hop = 0; hop < CLICK_HOPS + GEN_LISTEN_UTTERANCE_GAP_HOPS + 1; hop++) {
        const bool on = hop < CLICK_HOPS;
        TEST_ASSERT_EQUAL(ESP_OK, svc_listen_feed(on ? click : k_zeros, CLICK_SEQ + hop, on, false));
        n = drain(decided, n, &t);
    }
    TEST_ASSERT_EQUAL_MESSAGE(0, n, "the click was decided, not dropped");
    TEST_ASSERT_FALSE(svc_listen_busy());
    const svc_listen_commands_t next = {
        .texts = named.texts,
        .ids = named.ids,
        .n_commands = (uint16_t)named.n,
        .version = svc_listen_commands_version() + 1,
    };
    uint16_t unreadable = 0;
    TEST_ASSERT_EQUAL(ESP_OK, svc_listen_set_commands(&next, &unreadable));
    TEST_ASSERT_EQUAL(next.version, svc_listen_commands_version());
    esp_partition_munmap(handle);
}

TEST_CASE("svc_listen decides the open window FRAME_GAP when the chain breaks", "[svc_listen]")
{
    listen_head_t head;
    named_t named;
    esp_partition_mmap_handle_t handle;
    read_round(&head, &named, &handle);
    listen_on(&head, &named);
    uint32_t hop = 0;
    for (; hop < GEN_LISTEN_UTTERANCE_MIN_HOPS; hop++) {
        TEST_ASSERT_EQUAL(ESP_OK, svc_listen_feed(k_zeros, RESET_SEQ + hop, true, false));
    }
    TEST_ASSERT_TRUE(svc_listen_busy());
    TEST_ASSERT_EQUAL(ESP_OK, svc_listen_feed(k_zeros, RESET_SEQ + hop, false, true));
    svc_listen_decision_t d;
    bool decided = false;
    while (!decided && svc_listen_pending()) {
        decided = svc_listen_work(&d);
    }
    TEST_ASSERT_TRUE_MESSAGE(decided, "the holed window gave no decision");
    TEST_ASSERT_EQUAL(APP_EVT_REJECT, d.event.kind);
    TEST_ASSERT_EQUAL_STRING(APP_CODE_FRAME_GAP, d.event.code);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(RESET_SEQ + hop - 1, d.event.seq,
                                     "the decision ends on the window's last hop");
    TEST_ASSERT_FALSE_MESSAGE(svc_listen_busy(), "a window reached across the chain's break");
    esp_partition_munmap(handle);
}

TEST_CASE("svc_listen decides a closed window FRAME_GAP when the chain breaks before its pitch",
          "[svc_listen]")
{
    listen_head_t head;
    named_t named;
    esp_partition_mmap_handle_t handle;
    read_round(&head, &named, &handle);
    listen_on(&head, &named);
    if (!ai_engine_has(AI_ENGINE_MODEL_PITCH)) {
        esp_partition_munmap(handle);
        TEST_IGNORE_MESSAGE("no pitch in the image: a closed window has its pitch dims at once");
    }
    uint32_t hop = 0;
    for (; hop <= GEN_LISTEN_UTTERANCE_MIN_HOPS; hop++) {
        TEST_ASSERT_EQUAL(ESP_OK, svc_listen_feed(k_zeros, CLOSED_SEQ + hop, true, false));
    }
    const uint32_t last = hop;
    for (uint32_t quiet = 0; quiet <= GEN_LISTEN_UTTERANCE_GAP_HOPS; quiet++, hop++) {
        TEST_ASSERT_EQUAL(ESP_OK, svc_listen_feed(k_zeros, CLOSED_SEQ + hop, false, false));
    }
    TEST_ASSERT_EQUAL(ESP_OK, svc_listen_feed(k_zeros, CLOSED_SEQ + hop, false, true));
    svc_listen_decision_t d;
    bool decided = false;
    while (!decided && svc_listen_pending()) {
        decided = svc_listen_work(&d);
    }
    TEST_ASSERT_TRUE_MESSAGE(decided, "the closed window gave no decision");
    TEST_ASSERT_EQUAL_STRING(APP_CODE_FRAME_GAP, d.event.code);
    TEST_ASSERT_EQUAL_UINT32_MESSAGE(CLOSED_SEQ + last, d.event.seq,
                                     "the decision ends on the window's last hop");
    TEST_ASSERT_FALSE_MESSAGE(svc_listen_busy(), "a window waited for pitch the break cut off");
    esp_partition_munmap(handle);
}

// The round's roster, its owner alone, as a host sends it; afresh, everyone and every voiceprint goes first.
static void take_roster(const listen_head_t *head)
{
    static app_speaker_state_t state;
    static app_speaker_set_t set;
    TEST_ASSERT_EQUAL(ESP_OK, svc_listen_speaker_state(&state));
    memset(&set, 0, sizeof(set));
    set.version = state.version + 1;
    if (head->speaker & LISTEN_SPEAKER_AFRESH) {
        TEST_ASSERT_EQUAL(ESP_OK, svc_listen_speaker_set(&set));
        set.version++;
    }
    set.check = (head->speaker & LISTEN_SPEAKER_ON) != 0;
    set.count = 1;
    strlcpy(set.speakers[0].id, head->owner, sizeof(set.speakers[0].id));
    strlcpy(set.speakers[0].name, OWNER_NAME, sizeof(set.speakers[0].name));
    TEST_ASSERT_EQUAL(ESP_OK, svc_listen_speaker_set(&set));
    TEST_ASSERT_EQUAL(ESP_OK, svc_listen_speaker_state(&state));
    TEST_ASSERT_EQUAL(set.version, state.version);
    TEST_ASSERT_EQUAL(1, state.count);
}

TEST_CASE("svc_listen decides every Gate 3 session of the round as Python decides it, field by field",
          "[svc_listen]")
{
    listen_head_t head;
    named_t named;
    esp_partition_mmap_handle_t handle;
    read_round(&head, &named, &handle);
    listen_on(&head, &named);
    esp_partition_munmap(handle);
    // A round without the check turns it off, whatever NVS kept of an earlier run (KEHOACH 3.17).
    if (ai_engine_has(AI_ENGINE_MODEL_SPEAKER)) { take_roster(&head); }

    const void *mapped = NULL;
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
            at = run_session(at, base, &named, head.owner, &t);
            base += SEQ_APART;
            sessions++;
        }
        esp_partition_munmap(handle);
    }
    printf("svc_listen: %u sessions, %u windows, %u decided otherwise than python; work %" PRId64
           " us mean, %" PRId64 " us peak a window; decided %" PRId64 " us mean, %" PRId64
           " us peak after the close\n",
           (unsigned)sessions, (unsigned)t.windows, (unsigned)t.differ,
           t.windows ? t.work_us / (int64_t)t.windows : 0, t.work_peak_us,
           t.windows ? t.close_us / (int64_t)t.windows : 0, t.close_peak_us);
    printf("svc_listen: %u held for the speaker check, tail and noise sums %" PRId64 " us mean, %" PRId64
           " us peak\n",
           (unsigned)t.held, t.held ? t.hold_us / (int64_t)t.held : 0, t.hold_peak_us);
    TEST_ASSERT_EQUAL(0, t.differ);
}

void app_main(void)
{
    UNITY_BEGIN();
    unity_run_all_tests();
    UNITY_END();
}
