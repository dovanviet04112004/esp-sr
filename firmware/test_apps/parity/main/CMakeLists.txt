# ai_engine's kws and ctc decisions are pure C: compiled here alone, they bring no esp-dl into the parity app
# (KEHOACH 4.5.4).
set(ai_engine "${CMAKE_CURRENT_LIST_DIR}/../../../components/ai_engine")
set(ai_engine_src "${ai_engine}/src")
set(srcs "parity_main.c" "parity_common.c" "parity_stft.c" "parity_mel.c" "parity_pitch.c" "parity_chain.c"
    "parity_lang_vi.c" "parity_command_kws.c" "${ai_engine_src}/command_kws/decide.c"
    "parity_command_ctc.c" "${ai_engine_src}/command_ctc/ctc_score.c")
if(CONFIG_DSP_AFE_HPF_ENABLE)
    list(APPEND srcs "parity_hpf.c")
endif()
if(CONFIG_DSP_AFE_BALANCE_ENABLE)
    list(APPEND srcs "parity_balance.c")
endif()
if(CONFIG_DSP_AFE_DOA_ENABLE)
    list(APPEND srcs "parity_doa.c")
endif()
if(CONFIG_DSP_AFE_GSC_ENABLE)
    list(APPEND srcs "parity_gsc.c")
endif()
if(CONFIG_DSP_AFE_NS_OMLSA_ENABLE)
    list(APPEND srcs "parity_ns_omlsa.c")
endif()
if(CONFIG_DSP_AFE_VAD_ENABLE)
    list(APPEND srcs "parity_vad.c")
endif()
if(CONFIG_DSP_AFE_AGC_ENABLE)
    list(APPEND srcs "parity_agc.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "."
    PRIV_INCLUDE_DIRS "${ai_engine_src}" "${ai_engine}/include"
    REQUIRES dsp_spec dsp_afe lang_vi sys_storage esp_psram test_report
)
# The ctc decision matches its mirror rounding for rounding: no multiply-add fused into one rounding (KEHOACH 3.12).
set_source_files_properties("${ai_engine_src}/command_ctc/ctc_score.c" PROPERTIES COMPILE_OPTIONS "-ffp-contract=off")
# The modules profile builds firmware/sdkconfig.afe, whose chain is contracts/golden/chain_modules.
if(PARITY_PROFILE STREQUAL "modules")
    target_compile_definitions(${COMPONENT_LIB} PRIVATE PARITY_CHAIN_MODULES=1)
endif()
