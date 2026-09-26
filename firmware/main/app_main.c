#include "bsp_board.h"
#include "esp_app_desc.h"
#include "esp_log.h"
#include "gen_grid.h"

static const char *TAG = "app_main";

void app_main(void)
{
    ESP_ERROR_CHECK(bsp_board_init());
    ESP_LOGI(TAG, "esp-sr %s, grid 0x%08x", esp_app_get_description()->version, (unsigned)GEN_GRID_HASH);
}
