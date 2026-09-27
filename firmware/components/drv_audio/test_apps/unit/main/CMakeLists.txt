idf_component_register(
    SRCS "test_drv_audio.c"
    REQUIRES drv_audio esp_timer unity
    WHOLE_ARCHIVE
)
