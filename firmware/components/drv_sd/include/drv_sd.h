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
#define DRV_SD_NAME_COPIES_MAX 16 // past it, the last copy's name is written over

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

/** Name a new file stem.extension, or stem_1.extension and on while a name is taken: a lost NVS counts boots
 * again.
 *  @ctx nhin_task | blocking, a few ms a name | path is relative to DRV_SD_ROOT, path_bytes long
 *  @ret ESP_OK | ESP_ERR_INVALID_SIZE the name does not fit
 */
esp_err_t drv_sd_name(const char *stem, const char *extension, char *path, size_t path_bytes);

/** Write one file, its folders made as needed, then close it; a failed write leaves no file.
 *  @ctx nhin_task | blocking, tens of ms | path is relative to DRV_SD_ROOT
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no card | ESP_ERR_INVALID_SIZE path too long | ESP_FAIL card full or
 * gone
 */
esp_err_t drv_sd_write(const char *path, const uint8_t *data, size_t bytes);

/** Add data to the end of a file that drv_sd_write began, then close it.
 *  @ctx nhin_task | blocking, tens of ms | path is relative to DRV_SD_ROOT
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no card | ESP_ERR_INVALID_SIZE path too long | ESP_FAIL no such file,
 * card full or gone; the bytes already on the card stay
 */
esp_err_t drv_sd_append(const char *path, const uint8_t *data, size_t bytes);

/** Write data over a file's bytes from offset on, as a header filled in once its sizes are known.
 *  @ctx nhin_task | blocking, tens of ms | path is relative to DRV_SD_ROOT
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no card | ESP_ERR_INVALID_SIZE path too long | ESP_FAIL no such file
 * or gone
 */
esp_err_t drv_sd_patch(const char *path, size_t offset, const uint8_t *data, size_t bytes);

/** Read up to bytes from offset into data; got says how many came, 0 past the end.
 *  @ctx nhin_task | blocking, a few ms | path is relative to DRV_SD_ROOT
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no card | ESP_ERR_INVALID_SIZE path too long | ESP_FAIL no such file
 * or gone
 */
esp_err_t drv_sd_read(const char *path, size_t offset, uint8_t *data, size_t bytes, size_t *got);

#ifdef __cplusplus
}
#endif
