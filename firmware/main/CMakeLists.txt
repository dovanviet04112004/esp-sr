set(srcs "app_main.c" "app_boot.c" "app_tasks.c" "app_wiring.c")
if(CONFIG_APP_CONSOLE)
    list(APPEND srcs "app_console.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "."
    REQUIRES common bsp_board sys_storage drv_audio drv_camera drv_i2c drv_sd dsp_afe ai_engine lang_vi svc_front svc_journal svc_listen svc_note svc_report svc_sensors svc_speak svc_vision net_wifi net_mqtt esp_app_format esp_timer log console esp_wifi
)
