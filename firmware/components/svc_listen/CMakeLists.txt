idf_component_register(
    SRCS "src/svc_listen.c"
    INCLUDE_DIRS "include"
    REQUIRES common lang_vi
    PRIV_REQUIRES ai_engine dsp_spec esp_hw_support esp_timer log
)
