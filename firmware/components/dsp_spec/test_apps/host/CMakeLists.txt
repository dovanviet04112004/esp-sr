# dsp_spec_host_lib: dsp_spec plus dl_fft's portable C over shims for a few ESP-IDF headers (KEHOACH 4.5.7);
# dsp_spec_host runs it and the parity comparators of firmware/test_apps/parity, dsp_afe's host test links it.
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
# The PARITY lines go out through test_report, as on the board, so one reader judges both.
set(REPORT "${FIRMWARE}/test_apps/components/test_report")
add_library(test_report_host STATIC "${REPORT}/test_report.c")
target_include_directories(test_report_host PUBLIC "${REPORT}/include")
set(DL_FFT_SRCS
    "${DL_FFT_DIR}/dl_rfft_f32.c" "${DL_FFT_DIR}/dl_fft_f32.c" "${DL_FFT_DIR}/dl_rfft_s16.c" "${DL_FFT_DIR}/dl_fft_s16.c"
    "${DL_FFT_DIR}/base/dl_fft_base.c" "${DL_FFT_DIR}/base/dl_fft_table.c"
    "${DL_FFT_DIR}/base/dl_fft2r_fc32_ansi.c" "${DL_FFT_DIR}/base/dl_fft4r_fc32_ansi.c"
    "${DL_FFT_DIR}/base/dl_fft2r_sc16_ansi.c" "${DL_FFT_DIR}/base/dl_fft2r_sc16_dif_ansi.c")

add_library(dsp_spec_host_lib STATIC
    "${SPEC}/src/window.c" "${SPEC}/src/stft.c" "${SPEC}/src/mel.c" "${SPEC}/src/pitch.c" "${SPEC}/src/fft_dl.c"
    ${DL_FFT_SRCS})
target_include_directories(dsp_spec_host_lib
    PUBLIC "${CMAKE_CURRENT_LIST_DIR}/shim" "${SPEC}/include" "${FIRMWARE}/components/common/include"
    PRIVATE "${SPEC}/priv_include" "${DL_FFT_DIR}" "${DL_FFT_DIR}/base" "${DL_FFT_DIR}/base/isa")
set_target_properties(dsp_spec_host_lib PROPERTIES C_STANDARD 17 C_EXTENSIONS ON)
target_compile_options(dsp_spec_host_lib PRIVATE -Wall -Wextra -Werror -Wno-unused-parameter -Wno-sign-compare)
set_source_files_properties(${DL_FFT_SRCS} PROPERTIES COMPILE_OPTIONS "-Wno-error")
set_source_files_properties("${SPEC}/src/pitch.c" PROPERTIES COMPILE_OPTIONS "-ffp-contract=off")
target_link_libraries(dsp_spec_host_lib PUBLIC m)

add_executable(dsp_spec_host
    main/test_dsp_spec_host.c "${PARITY}/parity_common.c" "${PARITY}/parity_stft.c" "${PARITY}/parity_mel.c"
    "${PARITY}/parity_pitch.c")
target_include_directories(dsp_spec_host PRIVATE "${PARITY}")
set_target_properties(dsp_spec_host PROPERTIES C_STANDARD 17 C_EXTENSIONS ON)
target_compile_options(dsp_spec_host PRIVATE -Wall -Wextra -Werror -Wno-unused-parameter -Wno-sign-compare)
target_link_libraries(dsp_spec_host PRIVATE dsp_spec_host_lib test_report_host)
