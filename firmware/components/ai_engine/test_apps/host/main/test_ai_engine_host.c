#include <stdbool.h>
#include <stdio.h>

#include "ai_engine.h"

static unsigned s_failures;

static void check(bool ok, const char *what)
{
    printf("HOST %s: %s\n", ok ? "PASS" : "FAIL", what);
    s_failures += ok ? 0u : 1u;
}

int main(void)
{
    static const float log_mel[40];
    static int16_t pcm[16];
    static const uint8_t units[3] = {1, 2, 3};
    static ai_engine_lexicon_t lexicon;
    uint16_t score = 7;
    size_t n_samples = 9;
    ai_engine_command_result_t result;
    bool none = true;
    for (int m = 0; m < AI_ENGINE_MODEL_COUNT; m++) {
        none = none && !ai_engine_has((ai_engine_model_t)m);
    }
    check(ai_engine_load(0) == ESP_ERR_NOT_FOUND && ai_engine_load(1) == ESP_ERR_NOT_FOUND && none,
          "core shell: neither slot holds an image, no model present");
    check(ai_engine_ns_ops() == NULL, "ns shell: nothing to plug into the dsp_afe slot");
    ai_engine_wake_reset();
    check(ai_engine_wake_step(log_mel, &score) == ESP_ERR_INVALID_STATE &&
              ai_engine_wake_step(NULL, &score) == ESP_ERR_INVALID_ARG,
          "wake shell: no wake model, arguments still checked");
    check(ai_engine_command_begin() == ESP_ERR_INVALID_STATE &&
              ai_engine_command_step(log_mel) == ESP_ERR_INVALID_STATE &&
              ai_engine_command_score(&lexicon, &result) == ESP_ERR_INVALID_STATE,
          "command shell: no command model, so no window ever opens");
    check(ai_engine_synth_render(units, 3, pcm, 16, &n_samples) == ESP_ERR_NOT_SUPPORTED &&
              ai_engine_synth_render(units, 3, pcm, 16, NULL) == ESP_ERR_INVALID_ARG,
          "synth shell: no synth in the image");
    printf("HOST %u failure(s)\n", s_failures);
    return s_failures == 0 ? 0 : 1;
}
