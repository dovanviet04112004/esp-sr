# lang_vi on the host with plain CMake, over the ESP-IDF header shims of dsp_spec's host test (KEHOACH 4.5.7); with
# the golden root as argument it runs the parity comparators of firmware/test_apps/parity on every lang case.
cmake_minimum_required(VERSION 3.16)
project(lang_vi_host C)

set(FIRMWARE "${CMAKE_CURRENT_LIST_DIR}/../../../..")
set(LANG "${FIRMWARE}/components/lang_vi")
set(PARITY "${FIRMWARE}/test_apps/parity/main")
set(REPORT "${FIRMWARE}/test_apps/components/test_report")
add_executable(lang_vi_host main/test_lang_vi_host.c
    "${LANG}/src/normalize.c" "${LANG}/src/g2p.c" "${LANG}/src/lexicon.c"
    "${PARITY}/parity_common.c" "${PARITY}/parity_lang_vi.c" "${REPORT}/test_report.c")
target_include_directories(lang_vi_host PRIVATE
    "${LANG}/include" "${LANG}/priv_include" "${FIRMWARE}/components/common/include"
    "${FIRMWARE}/components/dsp_spec/test_apps/host/shim" "${PARITY}" "${REPORT}/include")
set_target_properties(lang_vi_host PROPERTIES C_STANDARD 17 C_EXTENSIONS ON)
target_compile_options(lang_vi_host PRIVATE -Wall -Wextra -Werror)
target_link_libraries(lang_vi_host PRIVATE m)
