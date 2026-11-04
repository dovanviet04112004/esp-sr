set(srcs "")
if(CONFIG_APP_CAMERA_ENABLE)
    list(APPEND srcs "src/drv_camera.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    REQUIRES common
    PRIV_REQUIRES bsp_board espressif__esp32-camera esp_timer
)
