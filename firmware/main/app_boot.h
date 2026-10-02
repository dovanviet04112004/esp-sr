/** The init chain of the device, from the board to the queues; test_apps/soak runs this same chain.
 *  @ctx task | blocking | holds no logic of its own (KEHOACH 4.5.2)
 */
#pragma once

#include "esp_err.h"
#include "gen_payload.h"
#include "svc_listen.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Bring up board, storage, wiring and audio capture, logging heap after each step.
 *  @ctx task | blocking | once from app_main, ahead of app_tasks_start
 *  @ret ESP_OK | the first failing step's error
 */
esp_err_t app_boot(void);

/** A parsed command set as svc_listen reads it, at boot from set.json and later from q_cmdset.
 *  @ctx any | non-blocking | texts and ids hold AI_ENGINE_COMMANDS_MAX; the result points into them and set
 */
svc_listen_commands_t app_boot_commands(const command_set_t *set, const char **texts, const char **ids);

#ifdef __cplusplus
}
#endif
