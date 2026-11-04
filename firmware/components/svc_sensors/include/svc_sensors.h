/** Board C's peripherals beside the camera, gathered onto up/sensors (KEHOACH 3.19).
 *  @ctx do_task after init | never creates a task
 */
#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t boot; // the boot counter, in every report
} svc_sensors_config_t;

/** Open each device; one that fails is logged and left out of the reports.
 *  @ctx app_boot | blocks on the buses | once
 */
void svc_sensors_init(const svc_sensors_config_t *cfg);

/** One tick of GEN_VISION_SENSORS_PERIOD_MS: read everything, report on a change or when the report is due.
 *  @ctx do_task | blocks on the I2C bus, never on the network
 */
void svc_sensors_step(void);

#ifdef __cplusplus
}
#endif
