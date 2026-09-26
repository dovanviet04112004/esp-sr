#include "app_boot.h"
#include "app_tasks.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#define SETTLE_MS 5000

void app_main(void)
{
    ESP_ERROR_CHECK(app_boot());
    ESP_ERROR_CHECK(app_tasks_start());
    vTaskDelay(pdMS_TO_TICKS(SETTLE_MS));
    app_tasks_log_watermarks();
}
