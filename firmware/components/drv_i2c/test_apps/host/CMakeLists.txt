# drv_i2c's two devices on a pretend bus, on the host with plain CMake (KEHOACH 4.5.7).
cmake_minimum_required(VERSION 3.16)
project(drv_i2c_host C)

set(I2C "${CMAKE_CURRENT_LIST_DIR}/../..")
add_executable(drv_i2c_host main/test_devices_host.c "${I2C}/src/mpu6500.c" "${I2C}/src/ads1115.c")
target_include_directories(drv_i2c_host PRIVATE "${I2C}/include" "${I2C}/../dsp_spec/test_apps/host/shim")
set_target_properties(drv_i2c_host PROPERTIES C_STANDARD 17 C_EXTENSIONS ON)
target_compile_options(drv_i2c_host PRIVATE -Wall -Wextra -Werror)
