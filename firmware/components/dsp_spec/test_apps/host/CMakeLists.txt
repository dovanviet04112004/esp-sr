# dsp_spec on the host with plain CMake: the component, dl_fft's portable C, and the parity comparators
# of firmware/test_apps/parity, over shims for the few ESP-IDF headers they include (KEHOACH 4.5.7).
cmake_minimum_required(VERSION 3.16)
project(dsp_spec_host C)

set(FIRMWARE "${CMAKE_CURRENT_LIST_DIR}/../../../..")
set(DL_FFT_DIR "${FIRMWARE}/managed_components/espressif__dl_fft"
    CACHE PATH "dl_fft sources, fetched into firmware/managed_components by an idf.py build of firmware/")
if(NOT EXISTS "${DL_FFT_DIR}/dl_rfft.h")
    message(FATAL_ERROR "no dl_fft at ${DL_FFT_DIR}: build firmware/ once with idf.py, or pass -DDL_FFT_DIR")
endif()

set(SPEC "${FIRMWARE}/components/dsp_spec")
set(PARITY "${FIRMWARE}/test_apps/parity/main")
add_executable(dsp_spec_host
    main/test_dsp_spec_host.c
    "${SPEC}/src/window.c" "${SPEC}/src/stft.c" "${SPEC}/src/mel.c" "${SPEC}/src/fft_dl.c"
    "${PARITY}/parity_common.c" "${PARITY}/parity_stft.c" "${PARITY}/parity_mel.c"
    "${DL_FFT_DIR}/dl_rfft_f32.c" "${DL_FFT_DIR}/dl_fft_f32.c" "${DL_FFT_DIR}/dl_rfft_s16.c" "${DL_FFT_DIR}/dl_fft_s16.c"
    "${DL_FFT_DIR}/base/dl_fft_base.c" "${DL_FFT_DIR}/base/dl_fft_table.c"
    "${DL_FFT_DIR}/base/dl_fft2r_fc32_ansi.c" "${DL_FFT_DIR}/base/dl_fft4r_fc32_ansi.c"
    "${DL_FFT_DIR}/base/dl_fft2r_sc16_ansi.c" "${DL_FFT_DIR}/base/dl_fft2r_sc16_dif_ansi.c")
target_include_directories(dsp_spec_host PRIVATE
    shim "${SPEC}/include" "${SPEC}/priv_include" "${FIRMWARE}/components/common/include" "${PARITY}"
    "${DL_FFT_DIR}" "${DL_FFT_DIR}/base" "${DL_FFT_DIR}/base/isa")
set_target_properties(dsp_spec_host PROPERTIES C_STANDARD 17 C_EXTENSIONS ON)
target_compile_options(dsp_spec_host PRIVATE -Wall -Wextra -Werror -Wno-unused-parameter -Wno-sign-compare)
set_source_files_properties("${DL_FFT_DIR}/dl_rfft_f32.c" "${DL_FFT_DIR}/dl_fft_f32.c" "${DL_FFT_DIR}/dl_rfft_s16.c"
    "${DL_FFT_DIR}/dl_fft_s16.c" "${DL_FFT_DIR}/base/dl_fft_base.c" "${DL_FFT_DIR}/base/dl_fft_table.c"
    "${DL_FFT_DIR}/base/dl_fft2r_fc32_ansi.c" "${DL_FFT_DIR}/base/dl_fft4r_fc32_ansi.c"
    "${DL_FFT_DIR}/base/dl_fft2r_sc16_ansi.c" "${DL_FFT_DIR}/base/dl_fft2r_sc16_dif_ansi.c"
    PROPERTIES COMPILE_OPTIONS "-Wno-error")
target_link_libraries(dsp_spec_host PRIVATE m)
