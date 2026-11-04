set(srcs "src/nmea.c")
if(CONFIG_APP_SENSORS_ENABLE)
    list(APPEND srcs "src/drv_gps.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    REQUIRES common
    PRIV_REQUIRES bsp_board esp_driver_uart
)
