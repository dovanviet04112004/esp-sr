idf_component_register(
    SRCS "board_c_main.c"
    PRIV_REQUIRES bsp_board drv_camera drv_sd esp_driver_gpio esp_driver_i2c esp_driver_i2s esp_driver_uart
                  esp_timer espressif__esp_jpeg
)
