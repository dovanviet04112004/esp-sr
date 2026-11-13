# drv_gps's pure NMEA reader on the host with plain CMake (KEHOACH 4.5.7).
cmake_minimum_required(VERSION 3.16)
project(drv_gps_host C)

set(GPS "${CMAKE_CURRENT_LIST_DIR}/../..")
add_executable(drv_gps_host main/test_nmea_host.c "${GPS}/src/nmea.c")
target_include_directories(drv_gps_host PRIVATE "${GPS}/include" "${GPS}/../common/include")
set_target_properties(drv_gps_host PROPERTIES C_STANDARD 17 C_EXTENSIONS ON)
target_compile_options(drv_gps_host PRIVATE -Wall -Wextra -Werror)
