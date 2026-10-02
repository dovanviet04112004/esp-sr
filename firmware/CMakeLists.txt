cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/third_party")
# A bare idf.py build takes the dsp_afe modules the product turns on, as every make target does (KEHOACH 4.9).
if(NOT DEFINED SDKCONFIG_DEFAULTS)
    set(SDKCONFIG_DEFAULTS "sdkconfig.defaults;sdkconfig.afe")
endif()

# The one source of the firmware version (KEHOACH 4.9).
set(PROJECT_VER "0.1.0")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(esp_sr)

# The default command set at /lfs/cmd/set.json, baked from contracts/ into the storage partition (KEHOACH 4.9, 6.4).
set(LFS_ROOT "${CMAKE_BINARY_DIR}/lfs_image")
file(REMOVE_RECURSE "${LFS_ROOT}")
configure_file("${CMAKE_CURRENT_LIST_DIR}/../contracts/commands/default_vi.json" "${LFS_ROOT}/cmd/set.json" COPYONLY)
littlefs_create_partition_image(storage "${LFS_ROOT}" FLASH_IN_PROJECT)
