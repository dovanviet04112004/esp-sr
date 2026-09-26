idf_component_register(
    SRCS "src/svc_front.c"
    INCLUDE_DIRS "include"
    REQUIRES common dsp_afe
    PRIV_REQUIRES esp_hw_support
)
