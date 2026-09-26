idf_component_register(
    SRCS "src/drv_audio.c"
    INCLUDE_DIRS "include"
    REQUIRES common
    PRIV_REQUIRES bsp_board esp_driver_i2s
)
