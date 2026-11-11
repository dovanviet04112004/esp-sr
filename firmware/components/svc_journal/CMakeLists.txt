set(srcs "")
if(CONFIG_APP_CAMERA_ENABLE)
    list(APPEND srcs "src/svc_journal.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    REQUIRES common net_mqtt
    PRIV_REQUIRES drv_sd svc_note svc_vision
)
