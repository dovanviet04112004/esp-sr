/** The thu and sach half of the front end: one dsp_afe instance fed with the frames drv_audio reads
 * (KEHOACH 3.2). Owns the instance and its memory; every call but init and stats belongs to sach_task
 * (KEHOACH 5.2).
 */
#pragma once

#include <stdint.h>

#include "dsp_afe.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t n_channels;           // drv_audio_channels(): 2, or 3 with the speaker reference
    const dsp_afe_calib_t *calib; // NULL skips balance; copied
    float ns_floor_db;
    float agc_target_dbfs;
    uint8_t vad_aggressiveness;
} svc_front_config_t;

/** Build dsp_afe with hot memory in internal RAM and cold memory in PSRAM, allocated once here.
 *  @ctx task | blocking, allocates | once at boot, ahead of sach_task
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE on a second call | ESP_ERR_NO_MEM | whatever dsp_afe_init refuses
 */
esp_err_t svc_front_init(const svc_front_config_t *cfg);

/** Run one hop of interleaved samples; a jump in seq resets dsp_afe first, so the frame carries the gap flag.
 *  out->seq is the capture seq the raw stream carries, so a logged window cuts out of it again (KEHOACH 5.4).
 *  @ctx sach_task | non-blocking | interleaved holds GEN_GRID_HOP_SAMPLES samples of n_channels each
 *  @ret ESP_OK with a clean frame in out | ESP_ERR_NOT_FOUND nothing ready | ESP_ERR_INVALID_STATE no init
 * yet
 */
esp_err_t svc_front_step(const int16_t *interleaved, uint32_t seq, dsp_afe_frame_t *out);

/** Copy the counters of the dsp_afe instance.
 *  @ctx any | non-blocking | a torn read across tasks is possible and harmless
 */
void svc_front_stats(dsp_afe_stats_t *out);

#ifdef __cplusplus
}
#endif
