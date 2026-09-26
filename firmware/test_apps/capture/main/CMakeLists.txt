idf_component_register(
    SRCS "capture_main.c"
    INCLUDE_DIRS "."
    REQUIRES common bsp_board drv_audio sys_storage net_wifi svc_report esp_app_format esp_psram
)
