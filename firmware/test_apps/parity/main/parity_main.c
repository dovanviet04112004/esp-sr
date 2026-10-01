#include <stdio.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "parity.h"
#include "sdkconfig.h"
#include "sys_storage.h"
#include "test_report.h"

#define GOLDEN_DIR STORAGE_LFS_MOUNT "/golden"
#define CASE_BYTES_MAX (512 * 1024)
#define REPORT_LINES_MAX 1024
#define PLAN_BYTES 128

static const struct {
    const char *block;
    parity_runner_t run;
} kBlocks[] = {
    {"stft", parity_stft},
    {"mel", parity_mel},
    {"pitch", parity_pitch},
    {"g2p", parity_g2p},
    {"normalize", parity_normalize},
    {"lexicon", parity_lexicon},
    {"command_kws", parity_command_kws},
    {"command_ctc", parity_command_ctc},
#if PARITY_CHAIN_MODULES
    {"chain_modules", parity_chain_modules},
#else
    {"chain", parity_chain},
#endif
#if CONFIG_DSP_AFE_HPF_ENABLE
    {"hpf", parity_hpf},
#endif
#if CONFIG_DSP_AFE_BALANCE_ENABLE
    {"balance", parity_balance},
#endif
#if CONFIG_DSP_AFE_DOA_ENABLE
    {"doa", parity_doa},
#endif
#if CONFIG_DSP_AFE_GSC_ENABLE
    {"gsc", parity_gsc},
#endif
#if CONFIG_DSP_AFE_NS_OMLSA_ENABLE
    {"ns_omlsa", parity_ns_omlsa},
#endif
#if CONFIG_DSP_AFE_VAD_ENABLE
    {"vad", parity_vad},
#endif
#if CONFIG_DSP_AFE_AGC_ENABLE
    {"agc", parity_agc},
#endif
};

static bool read_case(const char *path, void *buf, size_t cap, size_t *len)
{
    return sys_storage_read_file(path, buf, cap, len) == ESP_OK;
}

void app_main(void)
{
    if (!test_report_begin("PARITY", REPORT_LINES_MAX)) { return; }
    uint8_t *buf = heap_caps_malloc(CASE_BYTES_MAX, MALLOC_CAP_SPIRAM);
    if (sys_storage_init() != ESP_OK || buf == NULL) {
        test_report_line("error setup");
        test_report_serve();
    }
    char plan[PLAN_BYTES] = "plan";
    for (size_t i = 0; i < sizeof(kBlocks) / sizeof(kBlocks[0]); i++) {
        strncat(plan, " ", sizeof(plan) - strlen(plan) - 1);
        strncat(plan, kBlocks[i].block, sizeof(plan) - strlen(plan) - 1);
    }
    test_report_line("%s", plan);
    unsigned cases = 0;
    for (size_t i = 0; i < sizeof(kBlocks) / sizeof(kBlocks[0]); i++) {
        cases += parity_run_block(GOLDEN_DIR, kBlocks[i].block, kBlocks[i].run, read_case, buf,
                                  CASE_BYTES_MAX, NULL);
    }
    test_report_line("done %u cases", cases);
    test_report_serve();
}
