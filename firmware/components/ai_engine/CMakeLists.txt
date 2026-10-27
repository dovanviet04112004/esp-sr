set(srcs "src/core/engine.cpp" "src/core/model_image.cpp" "src/core/espdl_net.cpp" "src/core/espdl/espdl_ops.cpp"
         "src/core/espdl/espdl_softmax_quant.cpp" "src/core/espdl/espdl_layernorm_s8.cpp"
         "src/core/espdl/espdl_stage_sum.cpp" "src/core/espdl/stage_sum_s8_esp32s3.S"
         "src/core/espdl/espdl_conv_block.cpp" "src/core/espdl/conv_block_s8_esp32s3.S"
         "src/core/espdl/espdl_matmul_s8.cpp" "src/core/espdl/espdl_attention.cpp"
         "src/core/espdl/espdl_stem_conv.cpp" "src/core/espdl/softmax_s8_esp32s3.S"
         "src/core/espdl/espdl_stride_depthwise.cpp" "src/core/espdl/espdl_weighted_stats.cpp"
         "src/core/espdl/pool_s8_esp32s3.S"
         "src/core/espdl/conv_row_s8_esp32s3.S" "src/ns/ns.cpp"
         "src/wake/wake.cpp" "src/speaker/speaker.cpp" "src/speaker/score.c" "src/synth/synth.cpp")
if(CONFIG_AI_ENGINE_COMMAND_BACKEND_CTC)
    list(APPEND srcs "src/command_ctc/command.cpp" "src/command_ctc/ctc_score.c")
elseif(CONFIG_AI_ENGINE_COMMAND_BACKEND_RNNT)
    list(APPEND srcs "src/command_rnnt/command.cpp" "src/command_rnnt/rnnt_search.c" "src/command_ctc/ctc_score.c")
else()
    list(APPEND srcs "src/command/command.cpp")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src"
    REQUIRES common dsp_afe
    PRIV_REQUIRES dsp_spec sys_storage mbedtls log esp_timer espressif__esp-dl
)
# The decisions match their mirrors rounding for rounding: no multiply-add fused into one rounding (KEHOACH 3.12).
set_source_files_properties("src/command_ctc/ctc_score.c" "src/command_rnnt/rnnt_search.c" "src/speaker/score.c"
    "src/core/espdl/espdl_softmax_quant.cpp" "src/core/espdl/espdl_layernorm_s8.cpp"
    PROPERTIES COMPILE_OPTIONS "-ffp-contract=off")
