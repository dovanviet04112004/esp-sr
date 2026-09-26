#include "ai_engine.h"

// Neutral shell of E3-T4: E12-T1 decides whether a network renders speech at all (KEHOACH 3.13).

esp_err_t ai_engine_synth_render(const uint8_t *units, size_t n_units, int16_t *pcm, size_t cap,
                                 size_t *n_samples)
{
    if ((units == nullptr && n_units > 0) || (pcm == nullptr && cap > 0) || n_samples == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!ai_engine_has(AI_ENGINE_MODEL_SYNTH)) { return ESP_ERR_NOT_SUPPORTED; }
    *n_samples = 0;
    return ESP_OK;
}
