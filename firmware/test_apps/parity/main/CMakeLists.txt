set(srcs "parity_main.c" "parity_common.c" "parity_stft.c" "parity_mel.c" "parity_chain.c")
if(CONFIG_DSP_AFE_HPF_ENABLE)
    list(APPEND srcs "parity_hpf.c")
endif()
if(CONFIG_DSP_AFE_BALANCE_ENABLE)
    list(APPEND srcs "parity_balance.c")
endif()
if(CONFIG_DSP_AFE_VAD_ENABLE)
    list(APPEND srcs "parity_vad.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "."
    REQUIRES dsp_spec dsp_afe sys_storage esp_psram
)
