idf_component_register(
    SRCS "test_report.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES esp_driver_uart vfs
)
