#include "esp_err.h"
#include "sys_storage.h"
#include "unity.h"

void app_main(void)
{
    ESP_ERROR_CHECK(sys_storage_init());
    UNITY_BEGIN();
    unity_run_tests_by_tag("[ctc_net]", false);
    UNITY_END();
}
