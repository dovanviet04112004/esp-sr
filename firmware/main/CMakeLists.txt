idf_component_register(
    SRCS "app_main.c"
    INCLUDE_DIRS "."
    REQUIRES common bsp_board esp_app_format log
)
