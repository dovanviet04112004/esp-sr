# drv_ir's pure filter on the host with plain CMake (KEHOACH 4.5.7).
cmake_minimum_required(VERSION 3.16)
project(drv_ir_host C)

set(IR "${CMAKE_CURRENT_LIST_DIR}/../..")
add_executable(drv_ir_host main/test_filter_host.c "${IR}/src/filter.c")
target_include_directories(drv_ir_host PRIVATE "${IR}/include")
set_target_properties(drv_ir_host PROPERTIES C_STANDARD 17 C_EXTENSIONS ON)
target_compile_options(drv_ir_host PRIVATE -Wall -Wextra -Werror)
