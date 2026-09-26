/** Module switches of dsp_afe for the host build: none, or every module when DSP_AFE_HOST_ALL_MODULES is set.
 *  @ctx any | compile time; module numbers come from gen_afe.h, not from here
 */
#pragma once

#if DSP_AFE_HOST_ALL_MODULES
#define CONFIG_DSP_AFE_HPF_ENABLE 1
#define CONFIG_DSP_AFE_AEC_ENABLE 1
#define CONFIG_DSP_AFE_BALANCE_ENABLE 1
#define CONFIG_DSP_AFE_DOA_ENABLE 1
#define CONFIG_DSP_AFE_GSC_ENABLE 1
#define CONFIG_DSP_AFE_BSS_ENABLE 1
#define CONFIG_DSP_AFE_NS_OMLSA_ENABLE 1
#define CONFIG_DSP_AFE_VAD_ENABLE 1
#define CONFIG_DSP_AFE_AGC_ENABLE 1
#endif
