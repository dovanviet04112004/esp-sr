#include "core/branch.hpp"

// The host test runs without esp-dl: the speaker and pitch branches never build there.
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

void pitch_drop() noexcept
{}

esp_err_t pitch_load() noexcept
{
    return ESP_OK;
}

bool pitch_ready() noexcept
{
    return false;
}

} // namespace ai
