set(srcs "src/window.c" "src/stft.c" "src/mel.c" "src/pitch.c")
if(CONFIG_DSP_SPEC_FFT_DL_FFT)
    list(APPEND srcs "src/fft_dl.c")
else()
    list(APPEND srcs "src/fft.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "priv_include"
    REQUIRES common
    PRIV_REQUIRES espressif__dl_fft
)
# Every float32 step rounds once, as in the Python mirror, so the C matches it (KEHOACH 3.14, ADR-0020).
target_compile_options(${COMPONENT_LIB} PRIVATE -ffp-contract=off)
