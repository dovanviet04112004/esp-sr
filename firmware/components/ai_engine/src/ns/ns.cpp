#include "ai_engine.h"
#include "core/branch.hpp"

// No net built (AI_ENGINE_NS_BACKEND none): the slot runs the OM-LSA floor (KEHOACH 4.5.2).
namespace ai {

void ns_drop() noexcept
{}

esp_err_t ns_load() noexcept
{
    return ESP_OK;
}

bool ns_ready() noexcept
{
    return false;
}

} // namespace ai

const dsp_afe_ns_ops_t *ai_engine_ns_ops(void)
{
    return nullptr;
}
