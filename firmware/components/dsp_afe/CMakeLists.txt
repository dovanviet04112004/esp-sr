set(srcs "src/dsp_afe.c")
if(CONFIG_DSP_AFE_HPF_ENABLE)
    list(APPEND srcs "src/hpf.c")
endif()
if(CONFIG_DSP_AFE_AEC_ENABLE)
    list(APPEND srcs "src/aec.c")
endif()
if(CONFIG_DSP_AFE_BALANCE_ENABLE)
    list(APPEND srcs "src/balance.c")
endif()
if(CONFIG_DSP_AFE_DOA_ENABLE)
    list(APPEND srcs "src/doa.c")
endif()
if(CONFIG_DSP_AFE_GSC_ENABLE)
    list(APPEND srcs "src/gsc.c")
endif()
if(CONFIG_DSP_AFE_BSS_ENABLE)
    list(APPEND srcs "src/bss.c")
endif()
if(CONFIG_DSP_AFE_NS_OMLSA_ENABLE)
    list(APPEND srcs "src/ns_omlsa.c")
endif()
if(CONFIG_DSP_AFE_VAD_ENABLE)
    list(APPEND srcs "src/vad.c")
endif()
if(CONFIG_DSP_AFE_AGC_ENABLE)
    list(APPEND srcs "src/agc.c")
endif()

idf_component_register(
    SRCS ${srcs}
    INCLUDE_DIRS "include"
    PRIV_INCLUDE_DIRS "priv_include"
    REQUIRES common dsp_spec
)
# Every float32 step rounds once, as in the Python mirror, so the C matches it bit for bit (KEHOACH 3.14, ADR-0006).
target_compile_options(${COMPONENT_LIB} PRIVATE -ffp-contract=off)
