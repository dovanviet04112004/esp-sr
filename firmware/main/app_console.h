/** Development console on UART0: Wi-Fi credentials and NVS keys (KEHOACH 7.2).
 *  @ctx task | non-blocking | built only with APP_CONSOLE; prod carries no symbol of it
 */
#pragma once

#include "esp_err.h"
#include "sdkconfig.h"

#ifdef __cplusplus
extern "C" {
#endif

#if CONFIG_APP_CONSOLE
/** Register wifi and nvs, then start the REPL task pinned to core 0.
 *  @ctx task | non-blocking | once, after app_boot; the REPL runs until reboot
 *  @ret ESP_OK | an esp_console error
 */
esp_err_t app_console_start(void);
#else
/** Stand-in for a build without the console; inlined away.
 *  @ctx any | non-blocking
 */
static inline esp_err_t app_console_start(void)
{
    return ESP_ERR_NOT_SUPPORTED;
}
#endif

#ifdef __cplusplus
}
#endif
