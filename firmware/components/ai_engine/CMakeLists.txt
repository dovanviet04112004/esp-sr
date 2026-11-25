set(espdl "src/core/espdl")
idf_build_get_property(target IDF_TARGET)
set(srcs "src/core/engine.cpp" "src/core/model_image.cpp" "src/core/espdl_net.cpp" "${espdl}/espdl_ops.cpp"
         "${espdl}/ops/espdl_attention.cpp" "${espdl}/ops/espdl_band_block.cpp" "${espdl}/ops/espdl_band_chain.cpp"
         "${espdl}/ops/espdl_band_head.cpp"
         "${espdl}/ops/espdl_band_stem.cpp" "${espdl}/ops/espdl_band_tail.cpp" "${espdl}/ops/espdl_conv_block.cpp"
         "${espdl}/ops/espdl_c3k2_block.cpp" "${espdl}/ops/espdl_conv_lut.cpp"
         "${espdl}/ops/espdl_upsample_concat_conv.cpp"
         "${espdl}/ops/espdl_global_stats.cpp"
         "${espdl}/ops/espdl_group_norm_s8.cpp" "${espdl}/ops/espdl_layernorm_s8.cpp" "${espdl}/ops/espdl_matmul_s8.cpp"
         "${espdl}/ops/espdl_pointwise_s8.cpp" "${espdl}/ops/espdl_softmax_quant.cpp" "${espdl}/ops/espdl_stage_sum.cpp"
         "${espdl}/ops/espdl_stem_conv.cpp" "${espdl}/ops/espdl_stride_depthwise.cpp"
         "${espdl}/ops/espdl_time_depthwise.cpp" "${espdl}/ops/espdl_weighted_stats.cpp"
         "${espdl}/runtime/espdl_loan.cpp" "${espdl}/runtime/espdl_preload_${target}.cpp"
         "src/wake/wake.cpp" "src/wake/score.c" "src/speaker/speaker.cpp" "src/speaker/score.c" "src/synth/synth.cpp"
         "src/pitch/pitch.cpp" "src/pitch/decode.c" "src/detect/detect.cpp" "src/detect/decode.c")
if(CONFIG_AI_ENGINE_COMMAND_BACKEND_CTC)
    list(APPEND srcs "src/command_ctc/command.cpp" "src/command_ctc/ctc_score.c")
elseif(CONFIG_AI_ENGINE_COMMAND_BACKEND_RNNT)
    list(APPEND srcs "src/command_rnnt/command.cpp" "src/command_rnnt/rnnt_search.c" "src/command_ctc/ctc_score.c")
else()
    list(APPEND srcs "src/command/command.cpp")
endif()
if(CONFIG_AI_ENGINE_NS_BACKEND_GTCRN)
    set(ns_c "src/ns_gtcrn/gtcrn.c")
    list(APPEND srcs "src/ns_gtcrn/ns.cpp" ${ns_c})
elseif(CONFIG_AI_ENGINE_NS_BACKEND_ULUNAS)
    set(ns_c "src/ns_ulunas/ulunas.c")
    list(APPEND srcs "src/ns_ulunas/ns.cpp" ${ns_c})
else()
    set(ns_c "")
    list(APPEND srcs "src/ns/ns.cpp")
endif()
if(CONFIG_AI_ENGINE_CORES_TWO)
    list(APPEND srcs "${espdl}/runtime/espdl_split_two.cpp")
else()
    list(APPEND srcs "${espdl}/runtime/espdl_split_one.cpp")
endif()
if(CONFIG_AI_ENGINE_KERNELS_PIE)
    set(kernels "pie_esp32s3")
    list(APPEND srcs "${espdl}/kernels/band_s8_esp32s3.S" "${espdl}/kernels/depthwise_s8_esp32s3.S"
         "${espdl}/kernels/group_norm_s8_esp32s3.S"
         "${espdl}/kernels/layernorm_s8_esp32s3.S" "${espdl}/kernels/pointwise_fused_s8_esp32s3.S"
         "${espdl}/kernels/pointwise_s8_esp32s3.S" "${espdl}/kernels/softmax_s8_esp32s3.S"
         "${espdl}/kernels/stage_sum_s8_esp32s3.S" "${espdl}/kernels/stats_s8_esp32s3.S"
         "${espdl}/kernels/lut_s8_esp32s3.S" "${espdl}/kernels/lut_s16_esp32s3.S"
         "${espdl}/kernels/stem_s8_esp32s3.S")
else()
    set(kernels "c")
    set(kernels_c "${espdl}/kernels/band_s8.c" "${espdl}/kernels/depthwise_s8.c" "${espdl}/kernels/group_norm_s8.c"
        "${espdl}/kernels/layernorm_s8.c" "${espdl}/kernels/pointwise_fused_s8.c" "${espdl}/kernels/pointwise_s8.c"
        "${espdl}/kernels/softmax_s8.c" "${espdl}/kernels/stage_sum_s8.c" "${espdl}/kernels/stats_s8.c"
        "${espdl}/kernels/lut_s8.c" "${espdl}/kernels/lut_s16.c" "${espdl}/kernels/stem_s8.c")
    list(APPEND srcs ${kernels_c})
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "src" "${espdl}/kernels/impl/${kernels}" "${espdl}/chip/${target}"
    REQUIRES common dsp_afe dsp_spec
    PRIV_REQUIRES sys_storage mbedtls log esp_timer espressif__esp-dl
)
# The decisions match their mirrors rounding for rounding: no multiply-add fused into one rounding (KEHOACH 3.12).
set_source_files_properties("src/command_ctc/ctc_score.c" "src/command_rnnt/rnnt_search.c" "src/speaker/score.c"
    "src/pitch/decode.c" "src/wake/score.c" "src/detect/decode.c"
    "${espdl}/ops/espdl_softmax_quant.cpp" "${espdl}/ops/espdl_layernorm_s8.cpp" "${espdl}/ops/espdl_group_norm_s8.cpp"
    ${kernels_c} ${ns_c} PROPERTIES COMPILE_OPTIONS "-ffp-contract=off")
