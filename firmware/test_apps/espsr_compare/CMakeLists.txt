cmake_minimum_required(VERSION 3.16)

# Only the components this app builds: the manifest of ai_engine pins the product's esp-dl, and esp-sr needs a newer one.
set(FIRMWARE_COMPONENTS "${CMAKE_CURRENT_LIST_DIR}/../../components")
set(EXTRA_COMPONENT_DIRS "${FIRMWARE_COMPONENTS}/common" "${FIRMWARE_COMPONENTS}/dsp_spec" "${FIRMWARE_COMPONENTS}/dsp_afe"
                         "${FIRMWARE_COMPONENTS}/sys_storage" "${FIRMWARE_COMPONENTS}/net_wifi"
                         "${CMAKE_CURRENT_LIST_DIR}/../components/test_report")
set(COMPONENTS main dsp_spec dsp_afe sys_storage net_wifi test_report)

# One board: clock, cache, PSRAM and console from the firmware's own file; costs are reported at bench's compiler
# settings, which are prod's (KEHOACH 3.14).
set(SDKCONFIG_DEFAULTS "${CMAKE_CURRENT_LIST_DIR}/../../sdkconfig.defaults.esp32s3"
                       "${CMAKE_CURRENT_LIST_DIR}/../../sdkconfig.bench"
                       "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(espsr_compare)
