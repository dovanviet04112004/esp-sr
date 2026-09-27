# dsp_afe on the host with plain CMake, three times over dsp_spec_host_lib: every module off with the chain golden
# cases, every module on, and the product's modules of firmware/sdkconfig.afe with chain_modules (KEHOACH 4.5.7).
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
    target_compile_options(${name} PRIVATE -Wall -Wextra -Werror -Wno-sign-compare -ffp-contract=off)
    target_link_libraries(${name} PRIVATE dsp_spec_host_lib test_report_host)
endfunction()

set(PARITY "${FIRMWARE}/test_apps/parity/main")
add_afe_host(dsp_afe_host_off "${PARITY}/parity_common.c" "${PARITY}/parity_chain.c")
target_include_directories(dsp_afe_host_off PRIVATE "${PARITY}")
target_compile_definitions(dsp_afe_host_off PRIVATE DSP_AFE_HOST_GOLDEN=1)
add_afe_host(dsp_afe_host_on ${AFE_MODULE_SRCS} "${PARITY}/parity_common.c" "${PARITY}/parity_hpf.c"
              "${PARITY}/parity_balance.c" "${PARITY}/parity_vad.c" "${PARITY}/parity_agc.c")
target_include_directories(dsp_afe_host_on PRIVATE "${PARITY}")
target_compile_definitions(dsp_afe_host_on PRIVATE DSP_AFE_HOST_ALL_MODULES=1 DSP_AFE_HOST_GOLDEN=1)

# The switches firmware/sdkconfig.afe turns on become the same CONFIG_ definitions and module sources here.
file(STRINGS "${FIRMWARE}/sdkconfig.afe" PRODUCT_SWITCHES REGEX "^CONFIG_DSP_AFE_[A-Z_]+_ENABLE=y$")
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS "${FIRMWARE}/sdkconfig.afe")
set(PRODUCT_SRCS "")
set(PRODUCT_DEFS "")
foreach(switch IN LISTS PRODUCT_SWITCHES)
    string(REGEX REPLACE "^CONFIG_DSP_AFE_([A-Z_]+)_ENABLE=y$" "\\1" module "${switch}")
    string(TOLOWER "${module}" module)
    list(APPEND PRODUCT_SRCS "${AFE}/src/${module}.c")
    string(REGEX REPLACE "=y$" "=1" definition "${switch}")
    list(APPEND PRODUCT_DEFS "${definition}")
endforeach()
add_afe_host(dsp_afe_host_product ${PRODUCT_SRCS} "${PARITY}/parity_common.c" "${PARITY}/parity_chain.c")
target_include_directories(dsp_afe_host_product PRIVATE "${PARITY}")
target_compile_definitions(dsp_afe_host_product PRIVATE ${PRODUCT_DEFS} DSP_AFE_HOST_PRODUCT=1 DSP_AFE_HOST_GOLDEN=1)
