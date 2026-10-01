#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "command_ctc/ctc_score.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "unity.h"

extern const uint8_t ctc_decide_start[] asm("_binary_ctc_decide_bin_start");
extern const uint8_t ctc_decide_end[] asm("_binary_ctc_decide_bin_end");

// The head srpipe.tasks.command.ctc.postproc.ctc_score.DECIDE_HEAD packs, little-endian.
typedef struct __attribute__((packed)) {
    char magic[4];
    uint16_t n_classes, n_frames;
    uint8_t n_commands, variants_max, units_max, runs;
    uint16_t reject, margin;
} decide_head_t;

typedef struct __attribute__((packed)) {
    int16_t command;
    uint16_t score, margin, gap;
} decide_want_t;

static ai_engine_lexicon_t s_lexicon;

TEST_CASE("the ctc decision of the default commands over LENH's longest window matches its mirror, timed",
          "[ai_engine]")
{
    const uint8_t *p = ctc_decide_start;
    decide_head_t head;
    memcpy(&head, p, sizeof(head));
    p += sizeof(head);
    TEST_ASSERT_EQUAL_MEMORY("SRCD", head.magic, 4);
    const uint8_t *n_variants = p;
    const uint8_t *n_units = n_variants + head.n_commands;
    const uint8_t *units = n_units + head.n_commands * head.variants_max;
    p = units + head.n_commands * head.variants_max * head.units_max;
    p += (4 - (size_t)(p - ctc_decide_start) % 4) % 4;
    const size_t floats = (size_t)head.n_frames * head.n_classes;
    float *log_probs = heap_caps_malloc(floats * sizeof(float), MALLOC_CAP_SPIRAM);
    TEST_ASSERT_NOT_NULL(log_probs);
    memcpy(log_probs, p, floats * sizeof(float));
    p += floats * sizeof(float);
    decide_want_t want;
    memcpy(&want, p, sizeof(want));
    p += sizeof(want);
    float want_scores[AI_ENGINE_COMMANDS_MAX], got_scores[AI_ENGINE_COMMANDS_MAX];
    memcpy(want_scores, p, head.n_commands * sizeof(float));
    TEST_ASSERT_EQUAL_PTR(ctc_decide_end, p + head.n_commands * sizeof(float));

    s_lexicon.n_commands = head.n_commands;
    for (size_t c = 0; c < head.n_commands; c++) {
        s_lexicon.n_variants[c] = n_variants[c];
        for (size_t v = 0; v < head.variants_max; v++) {
            const size_t k = c * head.variants_max + v;
            s_lexicon.variants[c][v] =
                (ai_engine_seq_t){.n_units = n_units[k], .units = units + k * head.units_max};
        }
    }
    ai_engine_command_result_t out;
    TEST_ASSERT_EQUAL(ESP_OK,
                      ai_engine_command_ctc_decide(log_probs, head.n_classes, head.n_frames, &s_lexicon,
                                                   head.reject, head.margin, got_scores, &out));
    int64_t total_us = 0, peak_us = 0;
    for (uint8_t r = 0; r < head.runs; r++) {
        const int64_t started_us = esp_timer_get_time();
        ai_engine_command_ctc_decide(log_probs, head.n_classes, head.n_frames, &s_lexicon, head.reject,
                                     head.margin, got_scores, &out);
        const int64_t took_us = esp_timer_get_time() - started_us;
        total_us += took_us;
        peak_us = took_us > peak_us ? took_us : peak_us;
    }
    size_t forms = 0;
    for (size_t c = 0; c < head.n_commands; c++) {
        forms += n_variants[c];
    }
    printf("ctc_decide: %u commands, %u variants, %u frames x %u classes: %" PRId64 " us mean, %" PRId64
           " us peak over %u runs\n",
           head.n_commands, (unsigned)forms, head.n_frames, head.n_classes, total_us / head.runs, peak_us,
           head.runs);
    heap_caps_free(log_probs);
    TEST_ASSERT_EQUAL_INT16(want.command, out.command);
    TEST_ASSERT_EQUAL_UINT16(want.score, out.score_permille);
    TEST_ASSERT_EQUAL_UINT16(want.margin, out.margin_permille);
    TEST_ASSERT_EQUAL_UINT16(want.gap, out.free_gap_permille);
    TEST_ASSERT_EQUAL_MEMORY(want_scores, got_scores, head.n_commands * sizeof(float));
}
