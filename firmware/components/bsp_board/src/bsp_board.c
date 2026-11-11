#include "bsp_board.h"

#include <stdatomic.h>

#include "app_config.h"
#include "driver/gpio.h"

#ifdef APP_AMP_SD_GPIO
#define AMP_QUIET_GPIO APP_AMP_SD_GPIO
#else
// No shutdown pin and the microphone's clocks running: a floating DIN comes out of the speaker as noise.
#define AMP_QUIET_GPIO APP_I2S_DOUT_GPIO
#endif

#define LIT_BY_STREAM 1u
#define LIT_BY_CAMERA 2u
#define LIT_BY_NOTE 4u

static atomic_uint s_lit_by;

static void light(unsigned reason, bool on)
{
    const unsigned lit =
        on ? atomic_fetch_or(&s_lit_by, reason) | reason : atomic_fetch_and(&s_lit_by, ~reason) & ~reason;
    gpio_set_level(APP_LED_GPIO, lit != 0);
}

esp_err_t bsp_board_init(void)
{
    const gpio_config_t outputs = {
        .pin_bit_mask = (1ULL << AMP_QUIET_GPIO) | (1ULL << APP_LED_GPIO),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&outputs);
    if (err != ESP_OK) { return err; }
    gpio_set_level(AMP_QUIET_GPIO, 0);
    gpio_set_level(APP_LED_GPIO, 0);
    return ESP_OK;
}

void bsp_board_led(bool on)
{
    light(LIT_BY_STREAM, on);
}

void bsp_board_camera_light(bool on)
{
    light(LIT_BY_CAMERA, on);
}

void bsp_board_note_light(bool on)
{
    light(LIT_BY_NOTE, on);
}
