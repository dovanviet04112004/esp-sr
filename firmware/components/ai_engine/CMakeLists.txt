set(srcs "src/core/engine.cpp" "src/core/model_image.cpp" "src/core/espdl_net.cpp" "src/ns/ns.cpp" "src/wake/wake.cpp"
         "src/synth/synth.cpp")
if(CONFIG_AI_ENGINE_COMMAND_BACKEND_CTC)
    list(APPEND srcs "src/command_ctc/command.cpp" "src/command_ctc/ctc_score.c")
else()
    list(APPEND srcs "src/command/command.cpp")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src"
    REQUIRES common dsp_afe
    PRIV_REQUIRES sys_storage mbedtls log esp_timer espressif__esp-dl
)
# The ctc decision matches its mirror rounding for rounding: no multiply-add fused into one rounding (KEHOACH 3.12).
set_source_files_properties("src/command_ctc/ctc_score.c" PROPERTIES COMPILE_OPTIONS "-ffp-contract=off")
