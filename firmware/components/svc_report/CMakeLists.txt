set(srcs)
if(CONFIG_NET_STREAM_ENABLE)
    list(APPEND srcs "src/stream.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    REQUIRES common net_stream
    PRIV_REQUIRES esp_timer
)
