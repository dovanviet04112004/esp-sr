idf_component_register(
    SRCS "parity_main.c" "parity_common.c" "parity_stft.c" "parity_mel.c" "parity_chain.c"
    INCLUDE_DIRS "."
    REQUIRES dsp_spec dsp_afe sys_storage esp_psram
)
