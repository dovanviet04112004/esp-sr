#include "drv_ir.h"

#include "app_config.h"
#include "driver/gpio.h"
#include "drv_ir/filter.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "gen_vision.h"

#define US_PER_MS 1000

static drv_ir_filter_t s_filter;
static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static bool s_ready;

static void on_edge(void *arg)
{
    (void)arg;
    const int64_t now_us = esp_timer_get_time();
    const bool low = gpio_get_level(APP_IR_GPIO) == 0;
    portENTER_CRITICAL_ISR(&s_lock);
    drv_ir_filter_edge(&s_filter, now_us, low);
    portEXIT_CRITICAL_ISR(&s_lock);
}

esp_err_t drv_ir_init(void)
{
    if (s_ready) { return ESP_ERR_INVALID_STATE; }
    drv_ir_filter_reset(&s_filter, (int64_t)GEN_VISION_IR_RELEASE_MS * US_PER_MS);
    const gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << APP_IR_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
        .intr_type = GPIO_INTR_ANYEDGE,
    };
    esp_err_t err = gpio_config(&cfg);
    if (err != ESP_OK) { return err; }
    err = gpio_install_isr_service(0);
    if (err != ESP_OK && err != ESP_ERR_INVALID_STATE) { return err; }
    err = gpio_isr_handler_add(APP_IR_GPIO, on_edge, NULL);
    s_ready = err == ESP_OK;
    return err;
}

void drv_ir_read(drv_ir_state_t *state)
{
    const int64_t now_us = esp_timer_get_time();
    portENTER_CRITICAL(&s_lock);
    const bool chattering = drv_ir_filter_active(&s_filter, now_us);
    state->hits = s_filter.hits;
    portEXIT_CRITICAL(&s_lock);
    state->active = chattering || gpio_get_level(APP_IR_GPIO) == 0;
}
