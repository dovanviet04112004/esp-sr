#include "core/branch.hpp"

// The host test runs without esp-dl: the speaker branch never builds there.
namespace ai {

void speaker_drop() noexcept
{}

esp_err_t speaker_load() noexcept
{
    return ESP_OK;
}

bool speaker_ready() noexcept
{
    return false;
}

} // namespace ai
