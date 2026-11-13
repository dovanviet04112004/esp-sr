/** The board's UTC clock, set from GPS alone; file dates on the card in local time (KEHOACH 3.19).
 *  @ctx any task after init | never creates a task
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Set the zone of file dates and start with no time.
 *  @ctx app_boot | non-blocking | once
 *  @param tz POSIX TZ, copied by the C library
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_STATE a second call | ESP_ERR_NO_MEM
 */
esp_err_t sys_time_init(const char *tz);

/** Offer the UTC second of one RMC heard since the last offer, read at read_us of esp_timer.
 *  @ctx do_task | non-blocking, s_clock under a spinlock | settimeofday on the first offer, then once a
 *       GEN_VISION_CLOCK_WINDOW_S with the largest offset of the window
 */
void sys_time_offer(uint32_t utc_s, int64_t read_us);

/** UTC at uptime_us of this run's esp_timer, Unix ms.
 *  @ctx any | non-blocking, s_clock under a spinlock
 *  @ret false while no GPS time has come since boot
 */
bool sys_time_utc_ms(int64_t uptime_us, int64_t *utc_ms);

#ifdef __cplusplus
}
#endif
