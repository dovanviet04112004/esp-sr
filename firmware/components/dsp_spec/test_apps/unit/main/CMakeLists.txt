idf_component_register(
    SRCS "test_dsp_spec.c"
    REQUIRES dsp_spec unity esp_timer espressif__dl_fft
    WHOLE_ARCHIVE
)
