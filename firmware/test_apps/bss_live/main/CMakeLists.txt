idf_component_register(
    SRCS "bss_live_main.c"
    INCLUDE_DIRS "."
    REQUIRES common bsp_board drv_audio sys_storage net_wifi svc_report dsp_spec dsp_afe test_report esp_app_format
             esp_driver_uart esp_netif esp_psram vfs
)
