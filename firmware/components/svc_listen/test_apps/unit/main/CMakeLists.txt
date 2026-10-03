idf_component_register(
    SRCS "test_svc_listen.c"
    REQUIRES svc_listen ai_engine sys_storage unity esp_partition esp_timer common
    WHOLE_ARCHIVE
)
