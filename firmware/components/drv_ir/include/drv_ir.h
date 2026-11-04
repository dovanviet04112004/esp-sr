/** The infrared sensor of board C, its chatter filtered into hits (KEHOACH 3.19).
 *  @ctx do_task after init | never creates a task
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    bool active;   // something is in front of it now
    uint32_t hits; // since boot
} drv_ir_state_t;

/** Take the sensor's pin and watch its edges in an interrupt.
 *  @ctx app_boot | blocking | once
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE a second call | the GPIO driver's error
 */
esp_err_t drv_ir_init(void);

/** Read where the sensor stands.
 *  @ctx do_task | non-blocking
 */
void drv_ir_read(drv_ir_state_t *state);

#ifdef __cplusplus
}
#endif
