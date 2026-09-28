#include "ai_engine.h"
#include "core/model_image.hpp"

esp_err_t ai_engine_load(uint8_t slot)
{
    return ai::image_load(slot);
}

// Every branch is the neutral shell of E3-T4 until its network lands (E9-T5, E11-T11, E11-T12, E12-T4).
bool ai_engine_has(ai_engine_model_t model)
{
    (void)model;
    return false;
}
