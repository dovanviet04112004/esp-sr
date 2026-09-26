#include "ai_engine.h"

// Neutral shell of E3-T4: the wake network and its smoothing land in E11-T11 (KEHOACH 3.11).

esp_err_t ai_engine_wake_step(const float *log_mel, uint16_t *score_permille)
{
    if (log_mel == nullptr || score_permille == nullptr) { return ESP_ERR_INVALID_ARG; }
    if (!ai_engine_has(AI_ENGINE_MODEL_WAKE)) { return ESP_ERR_INVALID_STATE; }
    *score_permille = 0;
    return ESP_OK;
}

void ai_engine_wake_reset(void)
{}
