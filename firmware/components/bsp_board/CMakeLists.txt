idf_component_register(
    SRCS "src/bsp_board.c"
    INCLUDE_DIRS "include"
    REQUIRES common esp_driver_gpio
)
