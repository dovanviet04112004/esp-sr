cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../../../")
set(COMPONENTS main svc_listen ai_engine sys_storage unity)

# One board: clock, cache, PSRAM and console from the firmware's own file; timings at bench's compiler settings,
# which come last so they win (KEHOACH 4.5.8).
set(SDKCONFIG_DEFAULTS "${CMAKE_CURRENT_LIST_DIR}/../../../../sdkconfig.defaults.esp32s3"
                       "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults"
                       "${CMAKE_CURRENT_LIST_DIR}/../../../../sdkconfig.bench")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(svc_listen_unit)
