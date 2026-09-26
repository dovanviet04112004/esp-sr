idf_component_register(
    SRCS "src/net_wifi.c"
    INCLUDE_DIRS "include"
    REQUIRES common
    PRIV_REQUIRES sys_storage esp_wifi esp_netif esp_event esp_timer
)
