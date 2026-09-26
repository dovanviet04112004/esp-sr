# ai_engine on the host with plain CMake: the C++ sources behind the C facade, called from a C test
# over the ESP-IDF header shims of dsp_spec's host test (KEHOACH 4.5.7).
cmake_minimum_required(VERSION 3.16)
project(ai_engine_host C CXX)

set(FIRMWARE "${CMAKE_CURRENT_LIST_DIR}/../../../..")
set(ENGINE "${FIRMWARE}/components/ai_engine")
add_executable(ai_engine_host main/test_ai_engine_host.c
    "${ENGINE}/src/core/engine.cpp" "${ENGINE}/src/ns/ns.cpp" "${ENGINE}/src/wake/wake.cpp"
    "${ENGINE}/src/command/command.cpp" "${ENGINE}/src/synth/synth.cpp")
target_include_directories(ai_engine_host PRIVATE
    "${ENGINE}/include" "${FIRMWARE}/components/dsp_afe/include" "${FIRMWARE}/components/common/include"
    "${FIRMWARE}/components/dsp_spec/test_apps/host/shim")
set_target_properties(ai_engine_host PROPERTIES C_STANDARD 17 CXX_STANDARD 20 CXX_EXTENSIONS OFF)
target_compile_options(ai_engine_host PRIVATE -Wall -Wextra -Werror $<$<COMPILE_LANGUAGE:CXX>:-fno-exceptions -fno-rtti>)
