set(srcs)
if(CONFIG_NET_STREAM_ENABLE)
    list(APPEND srcs "src/net_stream.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    REQUIRES common
    PRIV_REQUIRES lwip
)
