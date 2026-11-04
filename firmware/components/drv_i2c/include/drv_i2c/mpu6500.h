/** The MPU-6500 at 0x68 on board C: acceleration, rotation and its own temperature (KEHOACH 3.19).
 *  @ctx do_task after drv_i2c_init | never creates a task
 */
#pragma once

#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DRV_I2C_MPU6500_AXES 3

typedef struct {
    int16_t accel_mg[DRV_I2C_MPU6500_AXES];
    int16_t gyro_dps_e1[DRV_I2C_MPU6500_AXES]; // tenths of a degree a second
    int16_t temp_c_e1;                         // tenths of a degree Celsius
} drv_i2c_mpu6500_sample_t;

/** Check the chip is one, then set its clock, ranges and low-pass filters.
 *  @ctx app_boot | blocks on the bus
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND another chip answers | the bus's error
 */
esp_err_t drv_i2c_mpu6500_init(void);

/** Read one sample of everything at once.
 *  @ctx do_task | blocks on the bus
 *  @ret ESP_OK | the bus's error
 */
esp_err_t drv_i2c_mpu6500_read(drv_i2c_mpu6500_sample_t *sample);

#ifdef __cplusplus
}
#endif
