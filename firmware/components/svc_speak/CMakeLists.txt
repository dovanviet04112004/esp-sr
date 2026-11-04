set(srcs "")
if(CONFIG_APP_SPEAKER_ENABLE)
    list(APPEND srcs "src/svc_speak.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    REQUIRES common
    PRIV_REQUIRES drv_audio
)
