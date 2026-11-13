/** The GPS of board C: UART1 receive only, the latest fix and UTC time on request (KEHOACH 3.19).
 *  @ctx do_task after init | never creates a task
 */
#pragma once

#include "drv_gps/nmea.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Install UART1 on the GPS pin.
 *  @ctx app_boot | blocking | once
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE a second call | the UART driver's error
 */
esp_err_t drv_gps_init(void);

/** Read what arrived and give the latest fix; has_time starts over after each call, so it means an RMC with
 *  a time came since the last one.
 *  @ctx do_task | non-blocking
 */
void drv_gps_poll(drv_gps_fix_t *fix);

#ifdef __cplusplus
}
#endif
