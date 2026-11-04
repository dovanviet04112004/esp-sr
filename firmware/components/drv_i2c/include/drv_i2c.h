/** The I2C bus of board C, each device on it in a file of its own (KEHOACH 3.19).
 *  @ctx any task after init | never creates a task | the bus driver's lock orders callers
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

/** Open the bus on its two pins.
 *  @ctx app_boot | blocking | once
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE a second call | the I2C driver's error
 */
esp_err_t drv_i2c_init(void);

/** Whether a device answers at this 7-bit address.
 *  @ctx task | blocks up to the bus timeout
 */
bool drv_i2c_present(uint8_t address);

/** Send command_bytes of command, then read out_bytes; no command reads straight away.
 *  @ctx task | blocks up to the bus timeout
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE without init | the I2C driver's error
 */
esp_err_t drv_i2c_read(uint8_t address, const uint8_t *command, size_t command_bytes, uint8_t *out,
                       size_t out_bytes);

/** Send n_bytes to a device.
 *  @ctx task | blocks up to the bus timeout
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE without init | the I2C driver's error
 */
esp_err_t drv_i2c_write(uint8_t address, const uint8_t *bytes, size_t n_bytes);

#ifdef __cplusplus
}
#endif
