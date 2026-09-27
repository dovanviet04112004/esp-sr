#include "drv_audio.h"

#include <stdatomic.h>
#include <stdbool.h>
#include <string.h>

#include "app_config.h"
#include "driver/i2s_std.h"
#include "esp_attr.h"
#include "freertos/FreeRTOS.h"
#include "gen_grid.h"

#define RX_CHANNELS 2
#define SLOT_WORDS (GEN_GRID_HOP_SAMPLES * RX_CHANNELS)

static i2s_chan_handle_t s_rx;
static uint8_t s_shift;
static uint32_t s_next_seq;
static uint32_t s_overflows_seen;
static uint32_t s_overflows_before_read;
static bool s_reading;
static _Atomic uint32_t s_overflows;
static drv_audio_stats_t s_stats;
static int32_t s_slots[SLOT_WORDS];

// Each overflow event is one DMA buffer dropped, and a buffer holds exactly one hop.
static IRAM_ATTR bool on_rx_overflow(i2s_chan_handle_t handle, i2s_event_data_t *event, void *ctx)
{
    atomic_fetch_add(&s_overflows, 1);
    return false;
}

esp_err_t drv_audio_init(const drv_audio_config_t *cfg)
{
    if (cfg == NULL || cfg->pcm_shift < DRV_AUDIO_PCM_SHIFT_MIN || cfg->pcm_shift > DRV_AUDIO_PCM_SHIFT_MAX ||
        cfg->dma_desc_num < 2) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_rx != NULL) { return ESP_ERR_INVALID_STATE; }
    if (cfg->enable_tx) { return ESP_ERR_NOT_SUPPORTED; }

    i2s_chan_config_t chan = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan.dma_desc_num = cfg->dma_desc_num;
    chan.dma_frame_num = GEN_GRID_HOP_SAMPLES;
    esp_err_t err = i2s_new_channel(&chan, NULL, &s_rx);
    if (err != ESP_OK) { return err; }

    // INMP441 speaks standard I2S: 24-bit data left-justified in 32-bit slots, 64 BCLK per WS.
    const i2s_std_config_t std = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(GEN_GRID_SAMPLE_RATE_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg =
            {
                .mclk = I2S_GPIO_UNUSED,
                .bclk = APP_I2S_BCLK_GPIO,
                .ws = APP_I2S_WS_GPIO,
                .dout = I2S_GPIO_UNUSED,
                .din = APP_I2S_DIN_GPIO,
            },
    };
    const i2s_event_callbacks_t callbacks = {.on_recv_q_ovf = on_rx_overflow};
    if ((err = i2s_channel_init_std_mode(s_rx, &std)) != ESP_OK ||
        (err = i2s_channel_register_event_callback(s_rx, &callbacks, NULL)) != ESP_OK ||
        (err = i2s_channel_enable(s_rx)) != ESP_OK) {
        i2s_del_channel(s_rx);
        s_rx = NULL;
        return err;
    }
    s_shift = cfg->pcm_shift;
    return ESP_OK;
}

uint8_t drv_audio_channels(void)
{
    return RX_CHANNELS;
}

esp_err_t drv_audio_read_frame(int16_t *interleaved, uint32_t *seq, uint32_t timeout_ms)
{
    if (s_rx == NULL || interleaved == NULL || seq == NULL) { return ESP_ERR_INVALID_STATE; }
    size_t got = 0;
    // The ring filled with no reader yet: drop what it holds, not hand the reader the whole ring in one
    // burst, and count seq and overflows from the first fresh hop.
    if (!s_reading) {
        while (i2s_channel_read(s_rx, s_slots, sizeof(s_slots), &got, 0) == ESP_OK &&
               got == sizeof(s_slots)) {}
        s_overflows_seen = s_overflows_before_read = atomic_load(&s_overflows);
        s_reading = true;
    }
    esp_err_t err = i2s_channel_read(s_rx, s_slots, sizeof(s_slots), &got, pdMS_TO_TICKS(timeout_ms));
    if (err != ESP_OK) { return err; }
    if (got != sizeof(s_slots)) { return ESP_ERR_INVALID_SIZE; }

    const uint32_t overflows = atomic_load(&s_overflows);
    const uint32_t lost = overflows - s_overflows_seen;
    s_overflows_seen = overflows;
    *seq = s_next_seq + lost;
    s_next_seq = *seq + 1;

    for (size_t i = 0; i < SLOT_WORDS; i++) {
        int32_t v = s_slots[i] >> s_shift;
        if (v > INT16_MAX || v < INT16_MIN) {
            v = v > 0 ? INT16_MAX : INT16_MIN;
            s_stats.clipped_samples++;
        }
        interleaved[i] = (int16_t)v;
    }
    s_stats.hops++;
    s_stats.dma_overflows = overflows - s_overflows_before_read;
    return ESP_OK;
}

esp_err_t drv_audio_write(const int16_t *mono, size_t n_samples, uint32_t timeout_ms)
{
    return ESP_ERR_NOT_SUPPORTED;
}

void drv_audio_stats(drv_audio_stats_t *out)
{
    if (out != NULL) { *out = s_stats; }
}
