set(srcs "")
if(CONFIG_APP_CAMERA_ENABLE)
    list(APPEND srcs "src/svc_capture.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    REQUIRES common net_mqtt svc_vision
    PRIV_REQUIRES esp_timer svc_note svc_outbox sys_time
)
