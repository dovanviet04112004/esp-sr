/** The OV5640 of board C: one JPEG at a time, the sensor resting between shots (KEHOACH 3.19).
 *  @ctx one task after init, nhin_task in the product | frames live in PSRAM until released
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    const uint8_t *jpeg;
    size_t bytes;
    uint16_t width;
    uint16_t height;
    void *held; // what drv_camera_release gives back
} drv_camera_frame_t;

/** Bring up the sensor, load autofocus firmware where CAMERA_AF_SUPPORT is on, and leave it resting.
 *  @ctx app_boot | blocking, under a second, ~4 s with autofocus | once
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE a second call | ESP_ERR_NOT_FOUND no sensor | esp32-camera's error
 */
esp_err_t drv_camera_init(void);

/** Leave the rest and wait for exposure and gain to hold still, GEN_VISION_SETTLE_FRAMES_MAX frames at most.
 *  @ctx nhin_task | blocking, a second or so
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no init yet | ESP_ERR_TIMEOUT no frame came
 */
esp_err_t drv_camera_wake(void);

/** Focus once, waiting at most GEN_VISION_FOCUS_TIMEOUT_MS; a lens without a motor has nothing to do here.
 *  @ctx nhin_task | blocking
 *  @ret ESP_OK | ESP_ERR_TIMEOUT never settled | ESP_ERR_NOT_SUPPORTED no autofocus firmware
 */
esp_err_t drv_camera_focus(void);

/** Take the next frame; it stays valid until drv_camera_release.
 *  @ctx nhin_task | blocking, a frame time | caller releases, one frame at a time
 *  @ret ESP_OK | ESP_ERR_TIMEOUT no frame came
 */
esp_err_t drv_camera_grab(drv_camera_frame_t *frame);

/** Put a JPEG segment right after the frame's start marker, inside the frame's own buffer.
 *  @ctx nhin_task | non-blocking | frame held
 *  @ret ESP_OK | ESP_ERR_NO_MEM no room left, the frame is left alone
 */
esp_err_t drv_camera_insert(drv_camera_frame_t *frame, const uint8_t *segment, size_t bytes);

/** Give a frame back; a frame already released or never grabbed is left alone.
 *  @ctx nhin_task | non-blocking
 */
void drv_camera_release(drv_camera_frame_t *frame);

/** Stop the sensor over SCCB, so it takes no PSRAM bandwidth from the listener.
 *  @ctx nhin_task | non-blocking
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no init yet | ESP_FAIL SCCB refused the write
 */
esp_err_t drv_camera_rest(void);

/** Read one sensor register, for the console's tuning.
 *  @ctx console, with no picture under way | blocking, one SCCB read
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no init yet | ESP_FAIL SCCB refused
 */
esp_err_t drv_camera_reg_get(uint16_t reg, uint8_t *value);

/** Write one sensor register, for the console's tuning; a restart forgets it.
 *  @ctx console, with no picture under way | blocking, one SCCB write
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE no init yet | ESP_FAIL SCCB refused
 */
esp_err_t drv_camera_reg_set(uint16_t reg, uint8_t value);

#ifdef __cplusplus
}
#endif
