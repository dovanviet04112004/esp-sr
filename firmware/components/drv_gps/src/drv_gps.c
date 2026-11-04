#include "drv_gps.h"

#include <stdbool.h>

#include "app_config.h"
#include "driver/uart.h"

#define GPS_UART UART_NUM_1
#define GPS_BAUD 9600
#define RX_BUFFER_BYTES 1024
#define READ_CHUNK_BYTES 128

static drv_gps_nmea_t s_nmea;
static bool s_ready;

esp_err_t drv_gps_init(void)
{
    if (s_ready) { return ESP_ERR_INVALID_STATE; }
    const uart_config_t cfg = {
        .baud_rate = GPS_BAUD,
        .data_bits = UART_DATA_8_BITS,
        .parity = UART_PARITY_DISABLE,
        .stop_bits = UART_STOP_BITS_1,
        .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
        .source_clk = UART_SCLK_DEFAULT,
    };
    esp_err_t err = uart_driver_install(GPS_UART, RX_BUFFER_BYTES, 0, 0, NULL, 0);
    if (err == ESP_OK) { err = uart_param_config(GPS_UART, &cfg); }
    if (err == ESP_OK) {
        err = uart_set_pin(GPS_UART, UART_PIN_NO_CHANGE, APP_GPS_RX_GPIO, UART_PIN_NO_CHANGE,
                           UART_PIN_NO_CHANGE);
    }
    if (err != ESP_OK) { return err; }
    drv_gps_nmea_reset(&s_nmea);
    s_ready = true;
    return ESP_OK;
}

void drv_gps_poll(drv_gps_fix_t *fix)
{
    uint8_t chunk[READ_CHUNK_BYTES];
    int got;
    while (s_ready && (got = uart_read_bytes(GPS_UART, chunk, sizeof(chunk), 0)) > 0) {
        for (int i = 0; i < got; i++) {
            drv_gps_nmea_feed(&s_nmea, chunk[i]);
        }
    }
    *fix = s_nmea.fix;
    s_nmea.fix.in_view = 0;
}
