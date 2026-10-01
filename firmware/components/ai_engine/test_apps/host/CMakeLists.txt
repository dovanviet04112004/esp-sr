# ai_engine on the host with plain CMake: the C++ sources behind the C facade, called from a C test over the
# ESP-IDF header shims of dsp_spec's host test (KEHOACH 4.5.7); and the kws and ctc decisions against contracts/golden through
# the parity comparators, alone with AI_ENGINE_HOST_PARITY_ONLY, which needs no OpenSSL (make parity-host).
cmake_minimum_required(VERSION 3.16)
project(ai_engine_host C CXX)
option(AI_ENGINE_HOST_PARITY_ONLY "Build the golden runner alone" OFF)

set(FIRMWARE "${CMAKE_CURRENT_LIST_DIR}/../../../..")
set(ENGINE "${FIRMWARE}/components/ai_engine")
set(PARITY "${FIRMWARE}/test_apps/parity/main")
set(REPORT "${FIRMWARE}/test_apps/components/test_report")
add_executable(ai_engine_parity_host main/parity_host.c "${ENGINE}/src/command_kws/decide.c"
    "${ENGINE}/src/command_ctc/ctc_score.c" "${PARITY}/parity_common.c" "${PARITY}/parity_command_kws.c"
    "${PARITY}/parity_command_ctc.c" "${REPORT}/test_report.c")
target_include_directories(ai_engine_parity_host PRIVATE
    "${ENGINE}/src" "${ENGINE}/include" "${FIRMWARE}/components/dsp_afe/include" "${FIRMWARE}/components/common/include"
    "${FIRMWARE}/components/dsp_spec/test_apps/host/shim" "${PARITY}" "${REPORT}/include")
set_target_properties(ai_engine_parity_host PROPERTIES C_STANDARD 17 C_EXTENSIONS ON)
target_compile_options(ai_engine_parity_host PRIVATE -Wall -Wextra -Werror)
# The ctc decision matches its mirror rounding for rounding: no multiply-add fused into one rounding (KEHOACH 3.12).
set_source_files_properties("${ENGINE}/src/command_ctc/ctc_score.c" PROPERTIES COMPILE_OPTIONS "-ffp-contract=off")
target_link_libraries(ai_engine_parity_host PRIVATE m)
if(AI_ENGINE_HOST_PARITY_ONLY)
    return()
endif()

find_package(OpenSSL REQUIRED COMPONENTS Crypto)
add_executable(ai_engine_host main/test_ai_engine_host.c main/fake_storage.c main/image_probe.cpp
    "${ENGINE}/src/core/engine.cpp" "${ENGINE}/src/core/model_image.cpp" "${ENGINE}/src/ns/ns.cpp"
    "${ENGINE}/src/wake/wake.cpp" "${ENGINE}/src/command/command.cpp" "${ENGINE}/src/synth/synth.cpp")
target_include_directories(ai_engine_host PRIVATE
    "${ENGINE}/include" "${ENGINE}/src" "${FIRMWARE}/components/dsp_afe/include"
    "${FIRMWARE}/components/common/include" "${FIRMWARE}/components/sys_storage/include"
    "${CMAKE_CURRENT_LIST_DIR}/shim" "${FIRMWARE}/components/dsp_spec/test_apps/host/shim")
target_link_libraries(ai_engine_host PRIVATE OpenSSL::Crypto)
set_target_properties(ai_engine_host PROPERTIES C_STANDARD 17 CXX_STANDARD 20 CXX_EXTENSIONS OFF)
# The toolchain's C library maps _Static_assert onto static_assert for C++; glibc leaves it to C.
target_compile_definitions(ai_engine_host PRIVATE $<$<COMPILE_LANGUAGE:CXX>:_Static_assert=static_assert>)
target_compile_options(ai_engine_host PRIVATE -Wall -Wextra -Werror $<$<COMPILE_LANGUAGE:CXX>:-fno-exceptions -fno-rtti>)
