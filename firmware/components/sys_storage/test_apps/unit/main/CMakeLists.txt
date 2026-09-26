idf_component_register(
    SRCS "test_sys_storage.c"
    REQUIRES sys_storage unity nvs_flash esp_partition esp_driver_gptimer esp_timer
    WHOLE_ARCHIVE
)
