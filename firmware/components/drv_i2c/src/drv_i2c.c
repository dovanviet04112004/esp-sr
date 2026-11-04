#include "drv_i2c.h"

#include "app_config.h"
#include "driver/i2c_master.h"

#define BUS_SPEED_HZ 100000
#define BUS_TIMEOUT_MS 50
#define GLITCH_CYCLES 7

static i2c_master_bus_handle_t s_bus;

esp_err_t drv_i2c_init(void)
{
    if (s_bus != NULL) { return ESP_ERR_INVALID_STATE; }
    // Port -1 takes the one the camera's SCCB left free; both wires have their pull-ups on the board.
    const i2c_master_bus_config_t cfg = {
        .i2c_port = -1,
        .sda_io_num = APP_I2C_SDA_GPIO,
        .scl_io_num = APP_I2C_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = GLITCH_CYCLES,
    };
    return i2c_new_master_bus(&cfg, &s_bus);
}

bool drv_i2c_present(uint8_t address)
{
    return s_bus != NULL && i2c_master_probe(s_bus, address, BUS_TIMEOUT_MS) == ESP_OK;
}

static esp_err_t open_device(uint8_t address, i2c_master_dev_handle_t *device)
{
    if (s_bus == NULL) { return ESP_ERR_INVALID_STATE; }
    const i2c_device_config_t cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = address,
        .scl_speed_hz = BUS_SPEED_HZ,
    };
    return i2c_master_bus_add_device(s_bus, &cfg, device);
}

esp_err_t drv_i2c_read(uint8_t address, const uint8_t *command, size_t command_bytes, uint8_t *out,
                       size_t out_bytes)
{
    i2c_master_dev_handle_t device = NULL;
    esp_err_t err = open_device(address, &device);
    if (err != ESP_OK) { return err; }
    err = command_bytes > 0
              ? i2c_master_transmit_receive(device, command, command_bytes, out, out_bytes, BUS_TIMEOUT_MS)
              : i2c_master_receive(device, out, out_bytes, BUS_TIMEOUT_MS);
    const esp_err_t closed = i2c_master_bus_rm_device(device);
    return err != ESP_OK ? err : closed;
}

esp_err_t drv_i2c_write(uint8_t address, const uint8_t *bytes, size_t n_bytes)
{
    i2c_master_dev_handle_t device = NULL;
    esp_err_t err = open_device(address, &device);
    if (err != ESP_OK) { return err; }
    err = i2c_master_transmit(device, bytes, n_bytes, BUS_TIMEOUT_MS);
    const esp_err_t closed = i2c_master_bus_rm_device(device);
    return err != ESP_OK ? err : closed;
}
