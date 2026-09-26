idf_component_register(
    SRCS "src/core/engine.cpp" "src/ns/ns.cpp" "src/wake/wake.cpp" "src/command/command.cpp" "src/synth/synth.cpp"
    INCLUDE_DIRS "include"
    REQUIRES common dsp_afe
)
