idf_component_register(
    SRCS "src/window.c" "src/stft.c" "src/mel.c" "src/pitch.c" "src/fft_dl.c"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "priv_include"
    REQUIRES common
    PRIV_REQUIRES espressif__dl_fft
)
# pitch matches srpipe.dsp.spec.pitch rounding for rounding: no multiply-add fused into one rounding (KEHOACH 3.14).
set_source_files_properties("src/pitch.c" PROPERTIES COMPILE_OPTIONS "-ffp-contract=off")
