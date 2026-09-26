/** Board bring-up that belongs to no driver: safe levels on the amplifier and LED pins (KEHOACH 4.5.4).
 *  @ctx task | blocking | once at boot, ahead of drv_audio_init
 */
#pragma once

#include <stdbool.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Drive APP_AMP_SD_GPIO low and APP_LED_GPIO off, so nothing clicks or glows during boot.
 *  @ctx task | non-blocking | once at boot
 *  @ret ESP_OK | an esp_driver_gpio error
 */
esp_err_t bsp_board_init(void);

/** Set the privacy LED; the only writer is the stream control path (KEHOACH 7.5).
 *  @ctx task | non-blocking
 */
void bsp_board_led(bool on);

#ifdef __cplusplus
}
#endif
