/** The init chain of the device, from the board to the queues; test_apps/soak runs this same chain.
 *  @ctx task | blocking | holds no logic of its own (KEHOACH 4.5.2)
 */
#pragma once

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Bring up board, storage, wiring and audio capture, logging heap after each step.
 *  @ctx task | blocking | once from app_main, ahead of app_tasks_start
 *  @ret ESP_OK | the first failing step's error
 */
esp_err_t app_boot(void);

#ifdef __cplusplus
}
#endif
