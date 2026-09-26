idf_component_register(
    SRCS "src/sys_storage.c"
    INCLUDE_DIRS "include"
    REQUIRES common
    PRIV_REQUIRES nvs_flash esp_partition joltwallet__littlefs esp_hw_support
)
