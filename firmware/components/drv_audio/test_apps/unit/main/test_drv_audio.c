#include <math.h>
#include <stdio.h>

#include "drv_audio.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gen_grid.h"
#include "unity.h"

#define HOPS_TEN_SECONDS 625
#define READ_TIMEOUT_MS 100
#define DMA_DESC_NUM 8
#define PCM_SHIFT_TOP16 16
#define SLOW_BOOT_MS 300 // longer than the 128 ms DMA ring

static int16_t s_hop[GEN_GRID_HOP_SAMPLES * 2];

static double dbfs(double rms)
{
    return 20.0 * log10(rms / 32768.0 + 1e-12);
}

TEST_CASE("after a slow boot, ten seconds arrive whole, in order, from two live and distinct microphones",
          "[drv_audio]")
{
    const drv_audio_config_t cfg = {
        .pcm_shift = PCM_SHIFT_TOP16, .enable_tx = false, .dma_desc_num = DMA_DESC_NUM};
    TEST_ASSERT_EQUAL(ESP_OK, drv_audio_init(&cfg));
    TEST_ASSERT_EQUAL(2, drv_audio_channels());
    vTaskDelay(pdMS_TO_TICKS(SLOW_BOOT_MS));

    double sum[2] = {0}, sum_sq[2] = {0}, diff_sq = 0;
    uint32_t seq = 0;
    for (uint32_t i = 0; i < HOPS_TEN_SECONDS; i++) {
        TEST_ASSERT_EQUAL(ESP_OK, drv_audio_read_frame(s_hop, &seq, READ_TIMEOUT_MS));
        TEST_ASSERT_EQUAL_UINT32(i, seq);
        for (int n = 0; n < GEN_GRID_HOP_SAMPLES; n++) {
            for (int ch = 0; ch < 2; ch++) {
                const double v = s_hop[2 * n + ch];
                sum[ch] += v;
                sum_sq[ch] += v * v;
            }
            const double d = (double)s_hop[2 * n] - s_hop[2 * n + 1];
            diff_sq += d * d;
        }
    }

    drv_audio_stats_t stats;
    drv_audio_stats(&stats);
    TEST_ASSERT_EQUAL_UINT32(0, stats.dma_overflows);
    TEST_ASSERT_EQUAL_UINT32(HOPS_TEN_SECONDS, stats.hops);

    const double n_samples = (double)HOPS_TEN_SECONDS * GEN_GRID_HOP_SAMPLES;
    for (int ch = 0; ch < 2; ch++) {
        const double mean = sum[ch] / n_samples;
        const double ac_rms = sqrt(sum_sq[ch] / n_samples - mean * mean);
        printf("MEASURE ch%d mean %.1f LSB, ac rms %.2f LSB = %.1f dBFS\n", ch, mean, ac_rms, dbfs(ac_rms));
        // A floating or dead line reads a constant; any real microphone shows its own noise.
        TEST_ASSERT_TRUE_MESSAGE(ac_rms > 0.3, "channel is flat: microphone missing or slot wrong");
    }
    printf("MEASURE ch0-ch1 rms %.2f LSB\n", sqrt(diff_sq / n_samples));
    TEST_ASSERT_TRUE_MESSAGE(diff_sq > 0, "both slots carry the same samples");
}

TEST_CASE("a second init is refused", "[drv_audio]")
{
    const drv_audio_config_t cfg = {
        .pcm_shift = PCM_SHIFT_TOP16, .enable_tx = false, .dma_desc_num = DMA_DESC_NUM};
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, drv_audio_init(&cfg));
}

TEST_CASE("the speaker path is refused until E10", "[drv_audio]")
{
    int16_t silence[4] = {0};
    TEST_ASSERT_EQUAL(ESP_ERR_NOT_SUPPORTED, drv_audio_write(silence, 4, 10));
}

void app_main(void)
{
    UNITY_BEGIN();
    unity_run_all_tests();
    UNITY_END();
}
