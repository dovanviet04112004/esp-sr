idf_component_register(
    SRCS "app_main.c" "app_boot.c" "app_tasks.c" "app_wiring.c"
    INCLUDE_DIRS "."
    REQUIRES common bsp_board sys_storage drv_audio dsp_afe net_mqtt esp_app_format log
)
