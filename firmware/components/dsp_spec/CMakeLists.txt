set(srcs "src/window.c" "src/stft.c" "src/mel.c")
if(CONFIG_DSP_SPEC_FFT_ESP_DSP)
    list(APPEND srcs "src/fft_dsp.c")
else()
    list(APPEND srcs "src/fft_dl.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "priv_include"
    REQUIRES common
    PRIV_REQUIRES espressif__dl_fft espressif__esp-dsp
)
