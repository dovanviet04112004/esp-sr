set(srcs "")
if(CONFIG_APP_CAMERA_ENABLE)
    list(APPEND srcs "src/drv_sd.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    REQUIRES common
    PRIV_REQUIRES bsp_board esp_driver_sdspi esp_driver_spi fatfs sdmmc vfs
)
