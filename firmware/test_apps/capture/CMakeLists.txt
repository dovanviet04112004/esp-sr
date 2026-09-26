cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../../components")
set(COMPONENTS main bsp_board drv_audio sys_storage net_wifi net_stream svc_report)

# One board: test apps take clock, cache, PSRAM and console settings from the firmware's own file.
set(SDKCONFIG_DEFAULTS "${CMAKE_CURRENT_LIST_DIR}/../../sdkconfig.defaults.esp32s3"
                       "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(capture)
