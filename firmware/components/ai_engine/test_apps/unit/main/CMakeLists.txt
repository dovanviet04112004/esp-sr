idf_component_register(
    SRCS "test_ai_engine.c"
    REQUIRES ai_engine sys_storage unity esp_partition esp_timer mbedtls
    WHOLE_ARCHIVE
)
