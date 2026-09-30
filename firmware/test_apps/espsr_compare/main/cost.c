#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gen_grid.h"
#include "sdkconfig.h"
#include "variants.h"

static uint64_t idle_us(void)
{
    uint64_t total = 0;
    for (BaseType_t core = 0; core < CONFIG_FREERTOS_NUMBER_OF_CORES; core++) {
        TaskStatus_t status;
        vTaskGetInfo(xTaskGetIdleTaskHandleForCore(core), &status, pdFALSE, eInvalid);
        total += status.ulRunTimeCounter;
    }
    return total;
}

void espsr_cost_begin(espsr_cost_t *cost)
{
    *cost = (espsr_cost_t){
        .internal_free = heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
        .psram_free = heap_caps_get_free_size(MALLOC_CAP_SPIRAM),
    };
}

void espsr_cost_start(espsr_cost_t *cost)
{
    cost->idle_us = idle_us();
    cost->start_us = esp_timer_get_time();
}

void espsr_cost_call(espsr_cost_t *cost, int64_t took_us)
{
    cost->calls++;
    cost->peak_us = took_us > cost->peak_us ? took_us : cost->peak_us;
}

void espsr_cost_memory(const espsr_cost_t *cost, espsr_job_result_t *r)
{
    r->internal_bytes = (uint32_t)(cost->internal_free - heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    r->psram_bytes = (uint32_t)(cost->psram_free - heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
}

void espsr_cost_end(espsr_cost_t *cost, size_t samples, size_t call_samples, espsr_job_result_t *r)
{
    const int64_t wall_us = esp_timer_get_time() - cost->start_us;
    // Run time counts in esp_timer microseconds (FREERTOS_RUN_TIME_STATS_USING_ESP_TIMER).
    const int64_t busy_us = CONFIG_FREERTOS_NUMBER_OF_CORES * wall_us - (int64_t)(idle_us() - cost->idle_us);
    r->us_mean = samples > 0 ? (uint32_t)(busy_us * GEN_GRID_HOP_SAMPLES / (int64_t)samples) : 0;
    r->us_peak =
        call_samples > 0 ? (uint32_t)(cost->peak_us * GEN_GRID_HOP_SAMPLES / (int64_t)call_samples) : 0;
}
