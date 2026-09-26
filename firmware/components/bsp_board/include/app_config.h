/** Every GPIO of board B, and only here (KEHOACH 2.2, CLAUDE.md 1.3).
 *  A change to any pin lands in the same commit as KEHOACH 2.2.
 */
#pragma once

#include "sdkconfig.h"

#define APP_I2S_BCLK_GPIO 19 // shares the pad with USB D-
#define APP_I2S_WS_GPIO 20   // shares the pad with USB D+
#define APP_I2S_DIN_GPIO 16  // both INMP441 on one line, L/R picks the slot
#define APP_I2S_DOUT_GPIO 17 // MAX98357A DIN, fitted at E10
#define APP_AMP_SD_GPIO 18   // MAX98357A shutdown, low mutes
#define APP_LED_GPIO 21      // lit while the audio stream is open
#define APP_BUTTON_GPIO 0    // BOOT button, read only after boot

#if (CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG || CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG) &&                  \
    (APP_I2S_BCLK_GPIO == 19 || APP_I2S_WS_GPIO == 20)
#error                                                                                                       \
    "GPIO 19/20 carry I2S on board B: use CONFIG_ESP_CONSOLE_UART_DEFAULT and CONFIG_ESP_CONSOLE_SECONDARY_NONE"
#endif
