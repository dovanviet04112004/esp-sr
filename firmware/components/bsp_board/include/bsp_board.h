/** Board bring-up that belongs to no driver: safe levels on the amplifier and LED pins (KEHOACH 4.5.4).
 *  @ctx task | blocking | once at boot, ahead of drv_audio_init
 */
#pragma once

#include <stdbool.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Drive the amplifier's shutdown pin, or its data pin on a board without one, low and APP_LED_GPIO off.
 *  @ctx task | non-blocking | once at boot
 *  @ret ESP_OK | an esp_driver_gpio error
 */
esp_err_t bsp_board_init(void);

/** Set the privacy LED for the audio stream; it stays lit while a capture still needs it (KEHOACH 7.5).
 *  @ctx task | non-blocking
 */
void bsp_board_led(bool on);

/** Light a capture: on board C the same lamp, so it shows a picture is being taken too (KEHOACH 3.19).
 *  @ctx nhin_task | non-blocking
 */
void bsp_board_camera_light(bool on);

/** Light a voice note: the same lamp stays lit while the camera or a note holds it (KEHOACH 7.5).
 *  @ctx ghi_task | non-blocking
 */
void bsp_board_note_light(bool on);

#ifdef __cplusplus
}
#endif
