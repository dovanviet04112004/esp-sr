set(srcs "app_main.c" "app_boot.c" "app_tasks.c" "app_wiring.c")
if(CONFIG_APP_CONSOLE)
    list(APPEND srcs "app_console.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "."
    REQUIRES common bsp_board sys_storage drv_audio dsp_afe net_mqtt esp_app_format log console esp_wifi
)
