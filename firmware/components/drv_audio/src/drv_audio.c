#include "drv_audio.h"

#include <stdatomic.h>
#include <stdbool.h>
#include <string.h>

#include "app_config.h"
#include "driver/i2s_std.h"
#include "esp_attr.h"
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gen_grid.h"

#define RX_CHANNELS_MAX 2
#define TX_RING_HOPS 32
#define TX_SLOT_SHIFT 16 // int16 at the top of a 32-bit slot
#define TX_WAIT_MS 4

static i2s_chan_handle_t s_rx;
static i2s_chan_handle_t s_tx;
static int16_t (*s_tx_ring)[GEN_GRID_HOP_SAMPLES]; // noi_task fills, thu_task empties
static _Atomic uint32_t s_tx_in;
static _Atomic uint32_t s_tx_out;
static int32_t s_tx_slots[GEN_GRID_HOP_SAMPLES];
static uint8_t s_mics;
static uint8_t s_channels;
static uint8_t s_shift;
static uint32_t s_next_seq;
static uint32_t s_overflows_seen;
static uint32_t s_overflows_before_read;
static bool s_reading;
static _Atomic uint32_t s_overflows;
static drv_audio_stats_t s_stats;
static int32_t s_slots[GEN_GRID_HOP_SAMPLES * RX_CHANNELS_MAX];

// Each overflow event is one DMA buffer dropped, and a buffer holds exactly one hop.
static IRAM_ATTR bool on_rx_overflow(i2s_chan_handle_t handle, i2s_event_data_t *event, void *ctx)
{
    atomic_fetch_add(&s_overflows, 1);
    return false;
}

esp_err_t drv_audio_init(const drv_audio_config_t *cfg)
{
    if (cfg == NULL || cfg->pcm_shift < DRV_AUDIO_PCM_SHIFT_MIN || cfg->pcm_shift > DRV_AUDIO_PCM_SHIFT_MAX ||
        cfg->dma_desc_num < 2 || cfg->mics > DRV_AUDIO_MICS_RIGHT) {
        return ESP_ERR_INVALID_ARG;
    }
    if (s_rx != NULL) { return ESP_ERR_INVALID_STATE; }
    if (cfg->enable_tx) {
        s_tx_ring = heap_caps_calloc(TX_RING_HOPS, sizeof(*s_tx_ring), MALLOC_CAP_SPIRAM);
        if (s_tx_ring == NULL) { return ESP_ERR_NO_MEM; }
    }

    i2s_chan_config_t chan = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan.dma_desc_num = cfg->dma_desc_num;
    chan.dma_frame_num = GEN_GRID_HOP_SAMPLES;
    chan.auto_clear_after_cb = true;
    esp_err_t err = i2s_new_channel(&chan, cfg->enable_tx ? &s_tx : NULL, &s_rx);
    if (err != ESP_OK) { return err; }

    const bool both = cfg->mics == DRV_AUDIO_MICS_BOTH;
    // INMP441 speaks standard I2S: 24-bit data left-justified in 32-bit slots, 64 BCLK per WS, mono or not.
    i2s_std_config_t std = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(GEN_GRID_SAMPLE_RATE_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT,
                                                        both ? I2S_SLOT_MODE_STEREO : I2S_SLOT_MODE_MONO),
        .gpio_cfg =
            {
                .mclk = I2S_GPIO_UNUSED,
                .bclk = APP_I2S_BCLK_GPIO,
                .ws = APP_I2S_WS_GPIO,
                .dout = cfg->enable_tx ? APP_I2S_DOUT_GPIO : I2S_GPIO_UNUSED,
                .din = APP_I2S_DIN_GPIO,
            },
    };
    // The S3's mono default enables both slots; enable only the microphone's (IDF i2s_std.h).
    if (!both) {
        std.slot_cfg.slot_mask = cfg->mics == DRV_AUDIO_MICS_RIGHT ? I2S_STD_SLOT_RIGHT : I2S_STD_SLOT_LEFT;
    }
    const i2s_event_callbacks_t callbacks = {.on_recv_q_ovf = on_rx_overflow};
    if (s_tx != NULL) {
        i2s_std_config_t tx_std = std;
        tx_std.slot_cfg.slot_mode = I2S_SLOT_MODE_MONO;
        tx_std.slot_cfg.slot_mask = I2S_STD_SLOT_BOTH;
        if ((err = i2s_channel_init_std_mode(s_tx, &tx_std)) != ESP_OK ||
            (err = i2s_channel_enable(s_tx)) != ESP_OK) {
            i2s_del_channel(s_tx);
            i2s_del_channel(s_rx);
            s_tx = s_rx = NULL;
            return err;
        }
    }
    if ((err = i2s_channel_init_std_mode(s_rx, &std)) != ESP_OK ||
        (err = i2s_channel_register_event_callback(s_rx, &callbacks, NULL)) != ESP_OK ||
        (err = i2s_channel_enable(s_rx)) != ESP_OK) {
        i2s_del_channel(s_rx);
        s_rx = NULL;
        return err;
    }
    s_shift = cfg->pcm_shift;
    s_mics = both ? RX_CHANNELS_MAX : 1;
    s_channels = s_mics + (cfg->enable_tx ? 1 : 0);
    return ESP_OK;
}

uint8_t drv_audio_channels(void)
{
    return s_channels;
}

// One TX hop per RX hop on the shared clock: the delay from here to the microphone stays constant, which
// is what aec's bulk delay stands on, and the ref channel is exactly what went out (KEHOACH 3.5).
static void play_next_hop(int16_t *interleaved)
{
    const uint32_t out = atomic_load(&s_tx_out);
    const bool playing = out != atomic_load(&s_tx_in);
    const int16_t *hop = s_tx_ring[out % TX_RING_HOPS];
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        const int16_t sample = playing ? hop[i] : 0;
        s_tx_slots[i] = (int32_t)sample << TX_SLOT_SHIFT;
        interleaved[i * s_channels + s_mics] = sample;
    }
    size_t wrote = 0;
    if (i2s_channel_write(s_tx, s_tx_slots, sizeof(s_tx_slots), &wrote, 0) != ESP_OK) {
        s_stats.tx_underruns++;
    }
    if (playing) { atomic_store(&s_tx_out, out + 1); }
}

esp_err_t drv_audio_read_frame(int16_t *interleaved, uint32_t *seq, uint32_t timeout_ms)
{
    if (s_rx == NULL || interleaved == NULL || seq == NULL) { return ESP_ERR_INVALID_STATE; }
    const size_t words = (size_t)GEN_GRID_HOP_SAMPLES * s_mics;
    const size_t bytes = words * sizeof(s_slots[0]);
    size_t got = 0;
    // The ring filled with no reader yet: drop what it holds, not hand the reader the whole ring in one
    // burst, and count seq and overflows from the first fresh hop.
    if (!s_reading) {
        while (i2s_channel_read(s_rx, s_slots, bytes, &got, 0) == ESP_OK && got == bytes) {}
        s_overflows_seen = s_overflows_before_read = atomic_load(&s_overflows);
        s_reading = true;
    }
    esp_err_t err = i2s_channel_read(s_rx, s_slots, bytes, &got, timeout_ms);
    if (err != ESP_OK) { return err; }
    if (got != bytes) { return ESP_ERR_INVALID_SIZE; }

    const uint32_t overflows = atomic_load(&s_overflows);
    const uint32_t lost = overflows - s_overflows_seen;
    s_overflows_seen = overflows;
    *seq = s_next_seq + lost;
    s_next_seq = *seq + 1;

    for (size_t i = 0; i < words; i++) {
        int32_t v = s_slots[i] >> s_shift;
        if (v > INT16_MAX || v < INT16_MIN) {
            v = v > 0 ? INT16_MAX : INT16_MIN;
            s_stats.clipped_samples++;
        }
        interleaved[(i / s_mics) * s_channels + i % s_mics] = (int16_t)v;
    }
    if (s_tx != NULL) { play_next_hop(interleaved); }
    s_stats.hops++;
    s_stats.dma_overflows = overflows - s_overflows_before_read;
    return ESP_OK;
}

esp_err_t drv_audio_write(const int16_t *mono, size_t n_samples, uint32_t timeout_ms)
{
    if (s_tx == NULL) { return ESP_ERR_NOT_SUPPORTED; }
    if (mono == NULL) { return ESP_ERR_INVALID_ARG; }
    uint32_t waited_ms = 0;
    while (n_samples > 0) {
        const uint32_t in = atomic_load(&s_tx_in);
        if (in - atomic_load(&s_tx_out) >= TX_RING_HOPS) {
            if (waited_ms >= timeout_ms) { return ESP_ERR_TIMEOUT; }
            vTaskDelay(pdMS_TO_TICKS(TX_WAIT_MS));
            waited_ms += TX_WAIT_MS;
            continue;
        }
        const size_t take = n_samples < GEN_GRID_HOP_SAMPLES ? n_samples : GEN_GRID_HOP_SAMPLES;
        int16_t *hop = s_tx_ring[in % TX_RING_HOPS];
        memcpy(hop, mono, take * sizeof(*hop));
        memset(hop + take, 0, (GEN_GRID_HOP_SAMPLES - take) * sizeof(*hop));
        atomic_store(&s_tx_in, in + 1);
        mono += take;
        n_samples -= take;
    }
    return ESP_OK;
}

void drv_audio_stats(drv_audio_stats_t *out)
{
    if (out != NULL) { *out = s_stats; }
}
