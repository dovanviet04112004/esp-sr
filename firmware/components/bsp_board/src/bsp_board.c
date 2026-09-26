#include "bsp_board.h"

#include "app_config.h"
#include "driver/gpio.h"

esp_err_t bsp_board_init(void)
{
    const gpio_config_t outputs = {
        .pin_bit_mask = (1ULL << APP_AMP_SD_GPIO) | (1ULL << APP_LED_GPIO),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&outputs);
    if (err != ESP_OK) { return err; }
    gpio_set_level(APP_AMP_SD_GPIO, 0);
    gpio_set_level(APP_LED_GPIO, 0);
    return ESP_OK;
}

void bsp_board_led(bool on)
{
    gpio_set_level(APP_LED_GPIO, on ? 1 : 0);
}
