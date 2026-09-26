cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../../components")
set(COMPONENTS main dsp_spec sys_storage)

# One board: test apps take clock, cache, PSRAM and console settings from the firmware's own file.
set(SDKCONFIG_DEFAULTS "${CMAKE_CURRENT_LIST_DIR}/../../sdkconfig.defaults.esp32s3"
                       "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(parity)

# KEHOACH 6.4 serves the cases from /lfs/golden, so the image root holds a golden/ directory.
set(GOLDEN_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../contracts/golden")
set(IMAGE_ROOT "${CMAKE_BINARY_DIR}/lfs_image")
file(GLOB_RECURSE GOLDEN_FILES CONFIGURE_DEPENDS "${GOLDEN_DIR}/*")
file(REMOVE_RECURSE "${IMAGE_ROOT}")
file(COPY "${GOLDEN_DIR}" DESTINATION "${IMAGE_ROOT}")
littlefs_create_partition_image(storage "${IMAGE_ROOT}" FLASH_IN_PROJECT)
