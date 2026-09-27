/** I2S0 for two INMP441 on one data line, and the speaker side once it is fitted (KEHOACH 2.2, 2.4).
 *  Delivers whole hops of GEN_GRID_HOP_SAMPLES per channel, 24-bit samples shifted to int16.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t pcm_shift;    // right shift of each 32-bit slot, 8..16 (KEHOACH 6.2)
    bool enable_tx;       // APP_SPEAKER_ENABLE; adds the ref channel
    uint8_t dma_desc_num; // 8 gives 128 ms of slack (KEHOACH 5.5)
} drv_audio_config_t;

typedef struct {
    uint32_t hops;
    uint32_t dma_overflows; // hops dropped since the first read
    uint32_t tx_underruns;
    uint32_t clipped_samples;
} drv_audio_stats_t;

/** Configure I2S0 on the pins of app_config.h and start the DMA.
 *  @ctx task | blocking | once at boot
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE already running | an esp_driver_i2s error
 */
esp_err_t drv_audio_init(const drv_audio_config_t *cfg);

/** Channels per hop: 2 for ch0 ch1, 3 when TX adds the aligned ref.
 *  @ctx any | non-blocking
 */
uint8_t drv_audio_channels(void);

/** Wait for the next hop and copy it interleaved; the first call drops the hops queued ahead of it, and seq
 * counts hops from the one it returns, gaps included.
 *  @ctx thu_task | blocking up to timeout_ms | caller owns out, channels x GEN_GRID_HOP_SAMPLES
 *  @ret ESP_OK | ESP_ERR_TIMEOUT no hop in time, which means the I2S clock stopped
 */
esp_err_t drv_audio_read_frame(int16_t *interleaved, uint32_t *seq, uint32_t timeout_ms);

/** Queue mono samples for the speaker; silence is sent whenever nothing is queued.
 *  @ctx noi_task | blocking up to timeout_ms for DMA space
 *  @ret ESP_OK | ESP_ERR_NOT_SUPPORTED TX disabled | ESP_ERR_TIMEOUT
 */
esp_err_t drv_audio_write(const int16_t *mono, size_t n_samples, uint32_t timeout_ms);

/** Copy the counters.
 *  @ctx any | non-blocking
 */
void drv_audio_stats(drv_audio_stats_t *out);

#ifdef __cplusplus
}
#endif
