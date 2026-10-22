cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../../components" "${CMAKE_CURRENT_LIST_DIR}/../components")
set(COMPONENTS main dsp_spec dsp_afe lang_vi sys_storage test_report)

# One board: test apps take clock, cache, PSRAM and console settings from the firmware's own file.
# Parity checks the code as it ships, so the compiler settings are those of bench, which are prod's (KEHOACH 3.14).
set(SDKCONFIG_DEFAULTS "${CMAKE_CURRENT_LIST_DIR}/../../sdkconfig.defaults.esp32s3"
                       "${CMAKE_CURRENT_LIST_DIR}/../../sdkconfig.bench")
# idf.py -D PARITY_PROFILE=modules turns on the modules the product turns on (firmware/sdkconfig.afe).
if(PARITY_PROFILE STREQUAL "modules")
    list(APPEND SDKCONFIG_DEFAULTS "${CMAKE_CURRENT_LIST_DIR}/../../sdkconfig.afe")
endif()
# The app's own defaults go last, so the modules it adds (bss) are not switched off by sdkconfig.afe.
list(APPEND SDKCONFIG_DEFAULTS "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(parity)

# KEHOACH 6.4 serves the cases from /lfs/golden, so the image root holds a golden/ directory.
set(GOLDEN_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../contracts/golden")
set(IMAGE_ROOT "${CMAKE_BINARY_DIR}/lfs_image")
file(GLOB_RECURSE GOLDEN_FILES CONFIGURE_DEPENDS "${GOLDEN_DIR}/*")
file(REMOVE_RECURSE "${IMAGE_ROOT}")
# The image holds the sets main/parity_main.c runs in this profile: all of them outgrew the 15.25 MB partition.
set(MODULE_SETS chain_modules chain_bss hpf balance doa gsc bss ns_omlsa vad agc)
file(GLOB GOLDEN_SETS RELATIVE "${GOLDEN_DIR}" "${GOLDEN_DIR}/*")
foreach(golden_set IN LISTS GOLDEN_SETS)
    list(FIND MODULE_SETS "${golden_set}" at)
    if(PARITY_PROFILE STREQUAL "modules")
        if(NOT at EQUAL -1)
            set(wanted ON)
        else()
            set(wanted OFF)
        endif()
    elseif(at EQUAL -1)
        set(wanted ON)
    else()
        set(wanted OFF)
    endif()
    if(wanted AND IS_DIRECTORY "${GOLDEN_DIR}/${golden_set}")
        file(COPY "${GOLDEN_DIR}/${golden_set}" DESTINATION "${IMAGE_ROOT}/golden")
    endif()
endforeach()
littlefs_create_partition_image(storage "${IMAGE_ROOT}" FLASH_IN_PROJECT)
