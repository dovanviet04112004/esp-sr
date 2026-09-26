# dsp_afe on the host with plain CMake, twice: every module off, as the skeleton app runs it, and every
# module on; both over dsp_spec_host_lib (KEHOACH 4.5.7).
cmake_minimum_required(VERSION 3.16)
project(dsp_afe_host C)

set(FIRMWARE "${CMAKE_CURRENT_LIST_DIR}/../../../..")
add_subdirectory("${FIRMWARE}/components/dsp_spec/test_apps/host" dsp_spec)

set(AFE "${FIRMWARE}/components/dsp_afe")
set(AFE_MODULES hpf aec balance doa gsc bss ns_omlsa vad agc)
list(TRANSFORM AFE_MODULES PREPEND "${AFE}/src/" OUTPUT_VARIABLE AFE_MODULE_SRCS)
list(TRANSFORM AFE_MODULE_SRCS APPEND ".c")

function(add_afe_host name)
    add_executable(${name} main/test_dsp_afe_host.c "${AFE}/src/dsp_afe.c" ${ARGN})
    target_include_directories(${name} PRIVATE shim "${AFE}/include" "${AFE}/priv_include")
    set_target_properties(${name} PROPERTIES C_STANDARD 17 C_EXTENSIONS ON)
    target_compile_options(${name} PRIVATE -Wall -Wextra -Werror -Wno-sign-compare)
    target_link_libraries(${name} PRIVATE dsp_spec_host_lib)
endfunction()

add_afe_host(dsp_afe_host_off)
add_afe_host(dsp_afe_host_on ${AFE_MODULE_SRCS})
target_compile_definitions(dsp_afe_host_on PRIVATE DSP_AFE_HOST_ALL_MODULES=1)
