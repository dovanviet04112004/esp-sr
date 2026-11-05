/** The SD card of board C on SPI, FAT under DRV_SD_ROOT (KEHOACH 3.19).
 *  @ctx one task after init, nhin_task in the product | a missing card is an error code, never a stall
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DRV_SD_ROOT "/sdcard"

/** Bring up SPI and mount the card; no card leaves the driver idle, and writes then refuse.
 *  @ctx app_boot | blocking, up to a second | once
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND no card or no FAT on it | ESP_ERR_INVALID_STATE a second call
 */
esp_err_t drv_sd_init(void);

/** Whether a card is mounted.
 *  @ctx any | non-blocking
 */
bool drv_sd_ready(void);

/** Whether a file is there, so a caller can keep from overwriting it.
 *  @ctx nhin_task | blocking, a few ms | path is relative to DRV_SD_ROOT
 */
bool drv_sd_exists(const char *path);

/** Write one file, its folders made as needed, then close it; a failed write leaves no file.
 *  @ctx nhin_task | blocking, tens of ms | path is relative to DRV_SD_ROOT
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no card | ESP_ERR_INVALID_SIZE path too long | ESP_FAIL card full or
 * gone
 */
esp_err_t drv_sd_write(const char *path, const uint8_t *data, size_t bytes);

#ifdef __cplusplus
}
#endif
