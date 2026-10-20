cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../../components" "${CMAKE_CURRENT_LIST_DIR}/../components")
set(COMPONENTS main bsp_board drv_audio sys_storage net_wifi net_stream svc_report dsp_spec dsp_afe test_report)

# The chain compiles as the product's does: bench's compiler settings, sdkconfig.afe's modules, then bss on top.
set(SDKCONFIG_DEFAULTS "${CMAKE_CURRENT_LIST_DIR}/../../sdkconfig.defaults.esp32s3"
                       "${CMAKE_CURRENT_LIST_DIR}/../../sdkconfig.bench"
                       "${CMAKE_CURRENT_LIST_DIR}/../../sdkconfig.afe"
                       "${CMAKE_CURRENT_LIST_DIR}/sdkconfig.defaults")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(bss_live)
