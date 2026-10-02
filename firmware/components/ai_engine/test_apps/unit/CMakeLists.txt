cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../../../")
set(COMPONENTS main ai_engine sys_storage unity)

# One board: clock, cache, PSRAM and console from the firmware's own file; the probes' costs are reported at bench's
# compiler settings, which come last so they win (KEHOACH 4.5.8).
set(SDKCONFIG_DEFAULTS "${CMAKE_CURRENT_LIST_DIR}/../../../../sdkconfig.defaults.esp32s3"
                       "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults"
                       "${CMAKE_CURRENT_LIST_DIR}/../../../../sdkconfig.bench")
# idf.py -D UNIT_PROFILE=rnnt builds the rnnt command backend in place of ctc, and its own suite (KEHOACH 4.5.2).
if(UNIT_PROFILE STREQUAL "rnnt")
    file(WRITE "${CMAKE_BINARY_DIR}/sdkconfig.rnnt" "CONFIG_AI_ENGINE_COMMAND_BACKEND_RNNT=y\n")
    list(APPEND SDKCONFIG_DEFAULTS "${CMAKE_BINARY_DIR}/sdkconfig.rnnt")
endif()

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(ai_engine_unit)
