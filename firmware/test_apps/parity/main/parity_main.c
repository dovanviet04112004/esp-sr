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
#define PLAN_BYTES 160 // a 192-byte report line holds it with tag and crc

static const struct {
    const char *block;
    parity_runner_t run;
} kBlocks[] = {
// Each build runs, and its LittleFS image holds, its own golden sets: one image of all of them outgrew the
// partition.
#if PARITY_CHAIN_MODULES
    {"chain_modules", parity_chain_modules},
#if CONFIG_DSP_AFE_BSS_ENABLE
    {"chain_bss", parity_chain_bss},
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
#if CONFIG_DSP_AFE_BSS_ENABLE
    {"bss", parity_bss},
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
#else
    {"stft", parity_stft},
    {"mel", parity_mel},
    {"spk_fbank", parity_spk_fbank},
    {"swift_front", parity_swift_front},
    {"swift_dims", parity_swift_dims},
    {"pitch_decode", parity_pitch_decode},
    {"wake_score", parity_wake_score},
    {"pitch", parity_pitch},
    {"g2p", parity_g2p},
    {"normalize", parity_normalize},
    {"lexicon", parity_lexicon},
    {"command_kws", parity_command_kws},
    {"command_ctc", parity_command_ctc},
    {"command_rnnt", parity_command_rnnt},
    {"speaker", parity_speaker},
    {"speaker_noise", parity_speaker_noise},
    {"chain", parity_chain},
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
        // The host judges only the blocks the plan names: a cut plan would leave the rest unjudged.
        if (strlen(plan) + 1 + strlen(kBlocks[i].block) >= sizeof(plan)) {
            test_report_line("error plan has no room for %s", kBlocks[i].block);
            test_report_serve();
        }
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
