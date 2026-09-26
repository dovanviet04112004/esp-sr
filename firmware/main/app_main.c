#include "app_boot.h"
#include "app_console.h"
#include "app_tasks.h"
#include "esp_err.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define SETTLE_MS 5000

static const char *TAG = "app_main";

void app_main(void)
{
    ESP_ERROR_CHECK(app_boot());
    ESP_ERROR_CHECK(app_tasks_start());
    const esp_err_t console = app_console_start();
    if (console != ESP_OK && console != ESP_ERR_NOT_SUPPORTED) {
        ESP_LOGE(TAG, "console: %s", esp_err_to_name(console));
    }
    vTaskDelay(pdMS_TO_TICKS(SETTLE_MS));
    app_tasks_log_watermarks();
}
