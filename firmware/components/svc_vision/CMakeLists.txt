set(srcs "")
if(CONFIG_APP_CAMERA_ENABLE)
    list(APPEND srcs "src/svc_vision.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    REQUIRES common net_mqtt
    PRIV_REQUIRES ai_engine bsp_board drv_camera drv_sd esp_timer espressif__esp_new_jpeg
)
