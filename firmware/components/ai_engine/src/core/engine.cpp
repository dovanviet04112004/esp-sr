#include "ai_engine.h"

// Neutral shell of E3-T4: no slot holds an image until E11-T9 writes the loader (KEHOACH 6.3).

esp_err_t ai_engine_load(uint8_t slot)
{
    (void)slot;
    return ESP_ERR_NOT_FOUND;
}

bool ai_engine_has(ai_engine_model_t model)
{
    (void)model;
    return false;
}
