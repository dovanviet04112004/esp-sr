idf_component_register(
    SRCS "parity_main.c" "parity_stft.c" "parity_mel.c"
    INCLUDE_DIRS "."
    REQUIRES dsp_spec sys_storage esp_psram
)
