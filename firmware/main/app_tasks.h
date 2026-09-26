/** The static task table of KEHOACH 5.2: the only place a task is created.
 *  @ctx task | blocking | core, priority and stack of every task come from that table
 */
#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Create every task of the table, pinned, on static stacks, and log the table.
 *  @ctx task | blocking | once, after app_boot; noi_task only with APP_SPEAKER_ENABLE
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE wiring not up or already started
 */
esp_err_t app_tasks_start(void);

/** Log how many stack bytes each task has never touched; a finished one-shot task shows its last value.
 *  @ctx task | non-blocking
 */
void app_tasks_log_watermarks(void);

#ifdef __cplusplus
}
#endif
