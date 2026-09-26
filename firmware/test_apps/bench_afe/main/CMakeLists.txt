idf_component_register(
    SRCS "bench_afe_main.c"
    INCLUDE_DIRS "."
    REQUIRES dsp_spec dsp_afe espressif__esp-dsp esp_app_format esp_rom esp_hw_support heap
)
