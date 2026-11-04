/** Every GPIO of boards B and C, and only here (KEHOACH 2.2, CLAUDE.md 1.3).
 *  A change to any pin lands in the same commit as KEHOACH 2.2.
 */
#pragma once

#include "sdkconfig.h"

#ifdef CONFIG_APP_BOARD_C
#define APP_I2S_BCLK_GPIO 9
#define APP_I2S_WS_GPIO 3    // strapping pin, read only with STRAP_JTAG_SEL
#define APP_I2S_DIN_GPIO 48  // one INMP441, the other slot floats
#define APP_I2S_DOUT_GPIO 14 // MAX98357A DIN, its SD pin is not wired
#define APP_LED_GPIO 42      // the lamp, lit while the audio stream is open
#define APP_BUTTON_GPIO 0    // the only button, read only after boot
#define APP_CAM_XCLK_GPIO 18
#define APP_CAM_PCLK_GPIO 15
#define APP_CAM_VSYNC_GPIO 40
#define APP_CAM_HREF_GPIO 41
#define APP_CAM_SIOD_GPIO 38
#define APP_CAM_SIOC_GPIO 39
#define APP_CAM_D0_GPIO 6
#define APP_CAM_D1_GPIO 4
#define APP_CAM_D2_GPIO 46 // strapping pin, picks the boot mode with GPIO 0
#define APP_CAM_D3_GPIO 5
#define APP_CAM_D4_GPIO 7
#define APP_CAM_D5_GPIO 16
#define APP_CAM_D6_GPIO 17
#define APP_CAM_D7_GPIO 8
#define APP_SD_CLK_GPIO 12
#define APP_SD_MISO_GPIO 13
#define APP_SD_MOSI_GPIO 11
#define APP_SD_CS_GPIO 10
#define APP_I2C_SDA_GPIO 1
#define APP_I2C_SCL_GPIO 2
#define APP_GPS_RX_GPIO 20 // shares the pad with USB D+
#define APP_IR_GPIO 19     // low on a hit, shares the pad with USB D-
#else
#define APP_I2S_BCLK_GPIO 19 // shares the pad with USB D-
#define APP_I2S_WS_GPIO 20   // shares the pad with USB D+
#define APP_I2S_DIN_GPIO 16  // both INMP441 on one line, L/R picks the slot
#define APP_I2S_DOUT_GPIO 17 // MAX98357A DIN, fitted at E10
#define APP_AMP_SD_GPIO 18   // MAX98357A shutdown, low mutes
#define APP_LED_GPIO 21      // lit while the audio stream is open
#define APP_BUTTON_GPIO 0    // BOOT button, read only after boot
#endif

#if CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG || CONFIG_ESP_CONSOLE_SECONDARY_USB_SERIAL_JTAG
#error                                                                                                       \
    "GPIO 19/20 carry signals on both boards: use CONFIG_ESP_CONSOLE_UART_DEFAULT and CONFIG_ESP_CONSOLE_SECONDARY_NONE"
#endif
