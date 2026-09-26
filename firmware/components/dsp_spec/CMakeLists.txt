idf_component_register(
    SRCS "src/window.c" "src/stft.c" "src/mel.c" "src/fft_dl.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "priv_include"
    REQUIRES common
    PRIV_REQUIRES espressif__dl_fft
)
