#include "drv_i2c/ads1115.h"

#include <stdbool.h>

#include "drv_i2c.h"

#define ADDRESS 0x48
#define REG_CONVERSION 0x00
#define REG_CONFIG 0x01
#define REG_LOW_THRESHOLD 0x02
#define REG_HIGH_THRESHOLD 0x03
#define LOW_THRESHOLD_AT_RESET 0x8000
#define HIGH_THRESHOLD_AT_RESET 0x7FFF
#define CONFIG_ONE_SHOT 0xC383 // AIN0 to ground, 4.096 V, 128 SPS, no comparator
#define CONFIG_CHANNEL_SHIFT 12
#define LSB_PER_MV 8 // 4.096 V over 32768

static esp_err_t read_word(uint8_t reg, uint16_t *value)
{
    uint8_t raw[2] = {0};
    const esp_err_t err = drv_i2c_read(ADDRESS, &reg, 1, raw, sizeof(raw));
    *value = (uint16_t)((uint16_t)raw[0] << 8 | raw[1]);
    return err;
}

static esp_err_t start_conversion(uint8_t channel)
{
    const uint16_t config = (uint16_t)(CONFIG_ONE_SHOT | (uint16_t)channel << CONFIG_CHANNEL_SHIFT);
    const uint8_t bytes[] = {REG_CONFIG, (uint8_t)(config >> 8), (uint8_t)config};
    return drv_i2c_write(ADDRESS, bytes, sizeof(bytes));
}

esp_err_t drv_i2c_ads1115_init(drv_i2c_ads1115_t *adc)
{
    *adc = (drv_i2c_ads1115_t){0};
    uint16_t low = 0;
    uint16_t high = 0;
    esp_err_t err = read_word(REG_LOW_THRESHOLD, &low);
    if (err == ESP_OK) { err = read_word(REG_HIGH_THRESHOLD, &high); }
    if (err != ESP_OK) { return err; }
    // Nothing here sets the thresholds, so they keep their reset values across a restart of the chip beside
    // them.
    if (low != LOW_THRESHOLD_AT_RESET || high != HIGH_THRESHOLD_AT_RESET) { return ESP_ERR_NOT_FOUND; }
    return start_conversion(0);
}

esp_err_t drv_i2c_ads1115_step(drv_i2c_ads1115_t *adc)
{
    uint16_t raw = 0;
    const esp_err_t err = read_word(REG_CONVERSION, &raw);
    if (err != ESP_OK) { return err; }
    adc->channel_mv[adc->converting] = (int16_t)((int16_t)raw / LSB_PER_MV);
    adc->converting = (uint8_t)((adc->converting + 1) % DRV_I2C_ADS1115_CHANNELS);
    return start_conversion(adc->converting);
}
