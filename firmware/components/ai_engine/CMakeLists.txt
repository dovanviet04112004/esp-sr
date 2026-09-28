idf_component_register(
    SRCS "src/core/engine.cpp" "src/core/model_image.cpp" "src/ns/ns.cpp" "src/wake/wake.cpp"
         "src/command/command.cpp" "src/synth/synth.cpp"
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src"
    REQUIRES common dsp_afe
    PRIV_REQUIRES sys_storage mbedtls log
)
