set(srcs "")
if(CONFIG_APP_SENSORS_ENABLE)
    list(APPEND srcs "src/drv_i2c.c" "src/mpu6500.c" "src/ads1115.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    REQUIRES common
    PRIV_REQUIRES bsp_board esp_driver_i2c
)
