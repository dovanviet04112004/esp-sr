#include "ai_engine.h"
#include "core/branch.hpp"
#include "core/model_image.hpp"

esp_err_t ai_engine_load(uint8_t slot)
{
    ai::command_drop();
    ai::speaker_drop();
    ai::pitch_drop();
    ai::wake_drop();
    esp_err_t err = ai::image_load(slot);
    if (err == ESP_OK) { err = ai::command_load(); }
    if (err == ESP_OK) { err = ai::speaker_load(); }
    if (err == ESP_OK) { err = ai::pitch_load(); }
    return err != ESP_OK ? err : ai::wake_load();
}

// Every other branch is the neutral shell of E3-T4 until its network lands (E9-T5, E12-T4).
bool ai_engine_has(ai_engine_model_t model)
{
    return (model == AI_ENGINE_MODEL_COMMAND && ai::command_ready()) ||
           (model == AI_ENGINE_MODEL_SPEAKER && ai::speaker_ready()) ||
           (model == AI_ENGINE_MODEL_PITCH && ai::pitch_ready()) ||
           (model == AI_ENGINE_MODEL_WAKE && ai::wake_ready());
}
