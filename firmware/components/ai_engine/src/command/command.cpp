#include "ai_engine.h"
#include "core/branch.hpp"

// Neutral shell of E3-T4 for the kws backend until E11-T17 runs its net (KEHOACH 3.12).

namespace {
bool s_window_open;
}

namespace ai {

void command_drop() noexcept
{}

esp_err_t command_load() noexcept
{
    return ESP_OK;
}

bool command_ready() noexcept
{
    return false;
}

} // namespace ai

esp_err_t ai_engine_command_prepare(const ai_engine_lexicon_t *lexicon)
{
    if (!ai_engine_has(AI_ENGINE_MODEL_COMMAND) || s_window_open) { return ESP_ERR_INVALID_STATE; }
    return lexicon != nullptr && lexicon->n_commands > 0 ? ESP_OK : ESP_ERR_INVALID_ARG;
}

esp_err_t ai_engine_command_begin(void)
{
    if (!ai_engine_has(AI_ENGINE_MODEL_COMMAND)) { return ESP_ERR_INVALID_STATE; }
    s_window_open = true;
    return ESP_OK;
}

esp_err_t ai_engine_command_step(const float *log_mel)
{
    if (log_mel == nullptr) { return ESP_ERR_INVALID_ARG; }
    return s_window_open ? ESP_OK : ESP_ERR_INVALID_STATE;
}

esp_err_t ai_engine_command_score(const ai_engine_lexicon_t *lexicon, ai_engine_command_result_t *out)
{
    if (lexicon == nullptr || out == nullptr) { return ESP_ERR_INVALID_ARG; }
    if (!s_window_open) { return ESP_ERR_INVALID_STATE; }
    s_window_open = false;
    *out = ai_engine_command_result_t{.command = -1,
                                      .score_permille = 0,
                                      .margin_permille = 0,
                                      .free_gap_permille = 0,
                                      .syllable_gap_permille = 0};
    return ESP_OK;
}

esp_err_t ai_engine_command_abort(void)
{
    if (!ai_engine_has(AI_ENGINE_MODEL_COMMAND)) { return ESP_ERR_INVALID_STATE; }
    s_window_open = false;
    return ESP_OK;
}

size_t ai_engine_command_features(void)
{
    return 0;
}
