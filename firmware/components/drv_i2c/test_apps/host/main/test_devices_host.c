#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "drv_i2c.h"
#include "drv_i2c/ads1115.h"
#include "drv_i2c/mpu6500.h"

#define MPU_ADDRESS 0x68
#define ADC_ADDRESS 0x48
#define REGISTERS 256
#define ADC_CONFIG 1

static uint8_t s_mpu[REGISTERS];
static uint16_t s_adc[4];
static int s_mpu_writes;
static uint16_t s_last_config;
static esp_err_t s_bus_error;

esp_err_t drv_i2c_read(uint8_t address, const uint8_t *command, size_t command_bytes, uint8_t *out,
                       size_t out_bytes)
{
    assert(command_bytes == 1);
    if (s_bus_error != ESP_OK) { return s_bus_error; }
    if (address == MPU_ADDRESS) {
        memcpy(out, &s_mpu[command[0]], out_bytes);
    } else {
        assert(address == ADC_ADDRESS && out_bytes == 2);
        out[0] = (uint8_t)(s_adc[command[0]] >> 8);
        out[1] = (uint8_t)s_adc[command[0]];
    }
    return ESP_OK;
}

esp_err_t drv_i2c_write(uint8_t address, const uint8_t *bytes, size_t n_bytes)
{
    if (s_bus_error != ESP_OK) { return s_bus_error; }
    if (address == MPU_ADDRESS) {
        assert(n_bytes == 2);
        s_mpu[bytes[0]] = bytes[1];
        s_mpu_writes++;
    } else {
        assert(n_bytes == 3 && bytes[0] == ADC_CONFIG);
        s_last_config = (uint16_t)((uint16_t)bytes[1] << 8 | bytes[2]);
    }
    return ESP_OK;
}

static void set_big_endian(uint8_t *at, int16_t value)
{
    at[0] = (uint8_t)((uint16_t)value >> 8);
    at[1] = (uint8_t)value;
}

static void test_an_mpu6500_is_set_up_and_reads_in_units(void)
{
    memset(s_mpu, 0, sizeof(s_mpu));
    s_mpu[0x75] = 0x70;
    s_mpu_writes = 0;
    assert(drv_i2c_mpu6500_init() == ESP_OK);
    assert(s_mpu_writes == 5 && s_mpu[0x6B] == 0x01 && s_mpu[0x1A] == 0x03 && s_mpu[0x1D] == 0x03);
    set_big_endian(&s_mpu[0x3B], -2064);  // the board's own reading: -0.126 g
    set_big_endian(&s_mpu[0x3D], -16224); // -0.990 g
    set_big_endian(&s_mpu[0x3F], 16384);  // 1 g
    set_big_endian(&s_mpu[0x41], 3584);   // 31.7 degrees
    set_big_endian(&s_mpu[0x43], 131);    // 1 deg/s
    set_big_endian(&s_mpu[0x45], -262);
    set_big_endian(&s_mpu[0x47], 0);
    drv_i2c_mpu6500_sample_t sample;
    assert(drv_i2c_mpu6500_read(&sample) == ESP_OK);
    assert(sample.accel_mg[0] == -125 && sample.accel_mg[1] == -990 && sample.accel_mg[2] == 1000);
    assert(sample.gyro_dps_e1[0] == 10 && sample.gyro_dps_e1[1] == -20 && sample.gyro_dps_e1[2] == 0);
    assert(sample.temp_c_e1 == 317);
}

static void test_negative_control_another_chip_at_0x68_is_refused_untouched(void)
{
    memset(s_mpu, 0, sizeof(s_mpu));
    s_mpu[0x75] = 0x68;
    s_mpu_writes = 0;
    assert(drv_i2c_mpu6500_init() == ESP_ERR_NOT_FOUND);
    assert(s_mpu_writes == 0);
}

static void test_the_adc_walks_its_inputs_a_step_at_a_time(void)
{
    s_adc[2] = 0x8000;
    s_adc[3] = 0x7FFF;
    drv_i2c_ads1115_t adc;
    assert(drv_i2c_ads1115_init(&adc) == ESP_OK);
    assert(s_last_config == 0xC383);
    const uint16_t raw[4] = {8000, 16, 0xFFF8, 26400}; // 1 V, 2 mV, -1 mV, 3.3 V
    for (int channel = 0; channel < 4; channel++) {
        s_adc[0] = raw[channel];
        assert(drv_i2c_ads1115_step(&adc) == ESP_OK);
    }
    assert(adc.channel_mv[0] == 1000 && adc.channel_mv[1] == 2 && adc.channel_mv[2] == -1 &&
           adc.channel_mv[3] == 3300);
    assert(adc.converting == 0 && s_last_config == 0xC383);
    s_adc[0] = 8000;
    assert(drv_i2c_ads1115_step(&adc) == ESP_OK);
    assert(s_last_config == 0xD383);
}

static void test_negative_control_thresholds_off_their_reset_values_are_refused(void)
{
    s_adc[2] = 0x0000;
    s_adc[3] = 0x7FFF;
    drv_i2c_ads1115_t adc;
    s_last_config = 0;
    assert(drv_i2c_ads1115_init(&adc) == ESP_ERR_NOT_FOUND);
    assert(s_last_config == 0);
}

static void test_a_bus_error_reaches_the_caller(void)
{
    s_bus_error = ESP_FAIL;
    drv_i2c_mpu6500_sample_t sample;
    drv_i2c_ads1115_t adc = {0};
    assert(drv_i2c_mpu6500_read(&sample) == ESP_FAIL);
    assert(drv_i2c_ads1115_step(&adc) == ESP_FAIL);
    assert(adc.converting == 0);
    s_bus_error = ESP_OK;
}

int main(void)
{
    test_an_mpu6500_is_set_up_and_reads_in_units();
    test_negative_control_another_chip_at_0x68_is_refused_untouched();
    test_the_adc_walks_its_inputs_a_step_at_a_time();
    test_negative_control_thresholds_off_their_reset_values_are_refused();
    test_a_bus_error_reaches_the_caller();
    puts("i2c devices: all passed");
    return 0;
}
