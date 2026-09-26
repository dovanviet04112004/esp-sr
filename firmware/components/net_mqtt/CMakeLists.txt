idf_component_register(
    SRCS "src/net_mqtt.c"
    INCLUDE_DIRS "include"
    REQUIRES common espressif__cjson
    PRIV_REQUIRES sys_storage espressif__mqtt
)
