/** I2S0 for one or two INMP441 on one data line, and the speaker side once it is fitted (KEHOACH 2.2, 2.4).
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

#define DRV_AUDIO_PCM_SHIFT_MIN 8  // keeps all 24 bits of the INMP441, saturating to int16
#define DRV_AUDIO_PCM_SHIFT_MAX 16 // keeps the top 16 of its 24 bits

typedef enum {
    DRV_AUDIO_MICS_BOTH = 0, // ch0 on the left slot, ch1 on the right
    DRV_AUDIO_MICS_LEFT,     // one microphone, L/R to GND
    DRV_AUDIO_MICS_RIGHT,    // one microphone, L/R to VDD
} drv_audio_mics_t;

typedef struct {
    uint8_t pcm_shift;     // right shift of each 32-bit slot (KEHOACH 6.2)
    bool enable_tx;        // APP_SPEAKER_ENABLE; adds the ref channel
    uint8_t dma_desc_num;  // 8 gives 128 ms of slack (KEHOACH 5.5)
    drv_audio_mics_t mics; // APP_MIC_COUNT and APP_MIC_SLOT (ADR-0042)
} drv_audio_config_t;

typedef struct {
    uint32_t hops;
    uint32_t dma_overflows; // hops dropped since the first read
    uint32_t tx_underruns;
    uint32_t tx_backlog_hops; // read in a burst after a stall, no TX written
    uint32_t clipped_samples;
} drv_audio_stats_t;

/** Configure I2S0 on the pins of app_config.h and start the DMA.
 *  @ctx task | blocking | once at boot
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE already running | an esp_driver_i2s error
 */
esp_err_t drv_audio_init(const drv_audio_config_t *cfg);

/** Channels per hop since init: the microphones, 1 or 2, then the ref, what TX sent in that hop.
 *  @ctx any | non-blocking
 */
uint8_t drv_audio_channels(void);

/** Wait for the next hop and copy it interleaved; the first call drops the hops queued ahead of it, and seq
 * counts hops from the one it returns, gaps included.
 *  @ctx thu_task | blocking up to timeout_ms | caller owns out, channels x GEN_GRID_HOP_SAMPLES
 *  @ret ESP_OK | ESP_ERR_TIMEOUT no hop in time, which means the I2S clock stopped
 */
esp_err_t drv_audio_read_frame(int16_t *interleaved, uint32_t *seq, uint32_t timeout_ms);

/** Queue mono samples for the speaker in whole hops, the last padded with silence; thu_task plays them.
 *  @ctx noi_task | blocking up to timeout_ms for ring space
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
