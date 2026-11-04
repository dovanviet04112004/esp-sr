set(srcs "")
if(CONFIG_APP_SENSORS_ENABLE)
    list(APPEND srcs "src/svc_sensors.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    REQUIRES common
    PRIV_REQUIRES drv_gps drv_i2c drv_ir esp_timer net_mqtt
)
