#include "drv_i2c/mpu6500.h"

#include <stddef.h>

#include "drv_i2c.h"

#define ADDRESS 0x68
#define REG_CONFIG 0x1A
#define REG_GYRO_CONFIG 0x1B
#define REG_ACCEL_CONFIG 0x1C
#define REG_ACCEL_CONFIG2 0x1D
#define REG_ACCEL_OUT 0x3B
#define REG_PWR_MGMT_1 0x6B
#define REG_WHO_AM_I 0x75
#define WHO_AM_I_MPU6500 0x70
#define CLOCK_BEST 0x01 // the gyro's PLL once it runs (datasheet)
#define RANGE_2G_250DPS 0x00
#define LOW_PASS_41_HZ 0x03 // one sample a second reads the motion, not a spike of it
#define ACCEL_OFFSET 0
#define TEMP_OFFSET 6
#define GYRO_OFFSET 8
#define SAMPLE_BYTES 14
#define ACCEL_LSB_PER_G 16384
#define MG_PER_G 1000
#define GYRO_LSB_PER_DPS 131
#define E1 10
#define TEMP_LSB_PER_C_E2 33387 // 333.87 LSB a degree, 21 degrees at zero (datasheet)
#define TEMP_ZERO_C_E1 210
#define E2_OVER_E1 100

static esp_err_t set_register(uint8_t reg, uint8_t value)
{
    const uint8_t bytes[] = {reg, value};
    return drv_i2c_write(ADDRESS, bytes, sizeof(bytes));
}

static int16_t big_endian(const uint8_t *bytes)
{
    return (int16_t)((uint16_t)bytes[0] << 8 | bytes[1]);
}

esp_err_t drv_i2c_mpu6500_init(void)
{
    const uint8_t who_am_i = REG_WHO_AM_I;
    uint8_t id = 0;
    esp_err_t err = drv_i2c_read(ADDRESS, &who_am_i, 1, &id, 1);
    if (err != ESP_OK) { return err; }
    if (id != WHO_AM_I_MPU6500) { return ESP_ERR_NOT_FOUND; }
    const uint8_t setup[][2] = {
        {REG_PWR_MGMT_1, CLOCK_BEST},        {REG_CONFIG, LOW_PASS_41_HZ},
        {REG_GYRO_CONFIG, RANGE_2G_250DPS},  {REG_ACCEL_CONFIG, RANGE_2G_250DPS},
        {REG_ACCEL_CONFIG2, LOW_PASS_41_HZ},
    };
    for (size_t i = 0; err == ESP_OK && i < sizeof(setup) / sizeof(setup[0]); i++) {
        err = set_register(setup[i][0], setup[i][1]);
    }
    return err;
}

esp_err_t drv_i2c_mpu6500_read(drv_i2c_mpu6500_sample_t *sample)
{
    const uint8_t first = REG_ACCEL_OUT;
    uint8_t raw[SAMPLE_BYTES];
    const esp_err_t err = drv_i2c_read(ADDRESS, &first, 1, raw, sizeof(raw));
    if (err != ESP_OK) { return err; }
    for (size_t axis = 0; axis < DRV_I2C_MPU6500_AXES; axis++) {
        const int32_t accel = big_endian(&raw[ACCEL_OFFSET + 2 * axis]);
        const int32_t gyro = big_endian(&raw[GYRO_OFFSET + 2 * axis]);
        sample->accel_mg[axis] = (int16_t)(accel * MG_PER_G / ACCEL_LSB_PER_G);
        sample->gyro_dps_e1[axis] = (int16_t)(gyro * E1 / GYRO_LSB_PER_DPS);
    }
    const int32_t temp = big_endian(&raw[TEMP_OFFSET]);
    sample->temp_c_e1 = (int16_t)(temp * E1 * E2_OVER_E1 / TEMP_LSB_PER_C_E2 + TEMP_ZERO_C_E1);
    return ESP_OK;
}
