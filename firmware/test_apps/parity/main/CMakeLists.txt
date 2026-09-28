set(srcs "parity_main.c" "parity_common.c" "parity_stft.c" "parity_mel.c" "parity_chain.c" "parity_lang_vi.c")
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
    REQUIRES dsp_spec dsp_afe lang_vi sys_storage esp_psram test_report
)
# The modules profile builds firmware/sdkconfig.afe, whose chain is contracts/golden/chain_modules.
if(PARITY_PROFILE STREQUAL "modules")
    target_compile_definitions(${COMPONENT_LIB} PRIVATE PARITY_CHAIN_MODULES=1)
endif()
