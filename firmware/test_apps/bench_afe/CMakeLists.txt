cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../../components" "${CMAKE_CURRENT_LIST_DIR}/../components")
set(COMPONENTS main dsp_spec dsp_afe test_report)

# Numbers are reported from the bench profile only (KEHOACH 4.5.8): its compiler settings come from the same file.
set(SDKCONFIG_DEFAULTS "${CMAKE_CURRENT_LIST_DIR}/../../sdkconfig.defaults.esp32s3"
                       "${CMAKE_CURRENT_LIST_DIR}/../../sdkconfig.bench"
                       "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(bench_afe)
