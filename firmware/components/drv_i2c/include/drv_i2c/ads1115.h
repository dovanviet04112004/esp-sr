/** The ADS1115 at 0x48 on board C: four inputs against ground, one read each step (KEHOACH 3.19).
 *  @ctx do_task after drv_i2c_init | never creates a task | the caller owns the state
 */
#pragma once

#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DRV_I2C_ADS1115_CHANNELS 4

typedef struct {
    int16_t channel_mv[DRV_I2C_ADS1115_CHANNELS];
    uint8_t converting; // the input whose result the next step reads
} drv_i2c_ads1115_t;

/** Check the chip is one and start the first conversion.
 *  @ctx app_boot | blocks on the bus
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND another chip answers | the bus's error
 */
esp_err_t drv_i2c_ads1115_init(drv_i2c_ads1115_t *adc);

/** Take the result of the conversion the last step started, then start the next input's.
 *  @ctx do_task | blocks on the bus | steps must lie at least 8 ms apart, one conversion's time
 *  @ret ESP_OK | the bus's error
 */
esp_err_t drv_i2c_ads1115_step(drv_i2c_ads1115_t *adc);

#ifdef __cplusplus
}
#endif
