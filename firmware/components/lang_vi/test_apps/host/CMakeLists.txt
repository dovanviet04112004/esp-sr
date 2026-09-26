# lang_vi on the host with plain CMake, over the ESP-IDF header shims of dsp_spec's host test (KEHOACH 4.5.7).
cmake_minimum_required(VERSION 3.16)
project(lang_vi_host C)

set(FIRMWARE "${CMAKE_CURRENT_LIST_DIR}/../../../..")
set(LANG "${FIRMWARE}/components/lang_vi")
add_executable(lang_vi_host main/test_lang_vi_host.c
    "${LANG}/src/normalize.c" "${LANG}/src/g2p.c" "${LANG}/src/lexicon.c")
target_include_directories(lang_vi_host PRIVATE
    "${LANG}/include" "${FIRMWARE}/components/common/include" "${FIRMWARE}/components/dsp_spec/test_apps/host/shim")
set_target_properties(lang_vi_host PROPERTIES C_STANDARD 17 C_EXTENSIONS ON)
target_compile_options(lang_vi_host PRIVATE -Wall -Wextra -Werror)
