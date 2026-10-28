/** The hooks ai_engine_load and ai_engine_has call on each branch the build carries; the directory Kconfig
 * picks implements them (KEHOACH 4.5.2), so the core names a branch, never a model.
 *  @ctx task | load blocks, at boot only, after image_load; ready is non-blocking
 */
#pragma once

#include "esp_err.h"

namespace ai {

/** Free whatever the command branch built over the image about to be dropped. */
void command_drop() noexcept;

/** Build the command branch over the loaded image; ESP_OK also when the image carries no command for it, or
 * one learned on another listen.yaml (KEHOACH 6.3).
 *  @ret ESP_OK | ESP_ERR_NO_MEM | ESP_ERR_NOT_SUPPORTED esp-dl refused the graph | ESP_ERR_INVALID_SIZE
 */
esp_err_t command_load() noexcept;

/** Whether command_load found the branch's entries and everything they need. */
bool command_ready() noexcept;

/** Free whatever the speaker branch built over the image about to be dropped. */
void speaker_drop() noexcept;

/** Build the speaker branch over the loaded image; ESP_OK also when the image carries no speaker, or one
 * imported on another speaker.yaml (KEHOACH 3.17).
 *  @ret ESP_OK | ESP_ERR_NO_MEM | ESP_ERR_NOT_SUPPORTED esp-dl refused the graph | ESP_ERR_INVALID_SIZE
 */
esp_err_t speaker_load() noexcept;

/** Whether speaker_load built the branch. */
bool speaker_ready() noexcept;

/** Free whatever the pitch branch built over the image about to be dropped. */
void pitch_drop() noexcept;

/** Build the pitch branch over the loaded image; ESP_OK also when the image carries no pitch, or a FRONT
 * entry whose tables do not fit (KEHOACH 3.11).
 *  @ret ESP_OK | ESP_ERR_NO_MEM | ESP_ERR_NOT_SUPPORTED esp-dl refused the graph | ESP_ERR_INVALID_SIZE
 */
esp_err_t pitch_load() noexcept;

/** Whether pitch_load built the branch. */
bool pitch_ready() noexcept;

} // namespace ai
