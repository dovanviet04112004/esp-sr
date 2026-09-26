#include <stdio.h>

#include "esp_heap_caps.h"
#include "parity.h"
#include "sys_storage.h"

#define GOLDEN_DIR STORAGE_LFS_MOUNT "/golden"
#define CASE_BYTES_MAX (128 * 1024)

static const struct {
    const char *block;
    parity_runner_t run;
} kBlocks[] = {
    {"stft", parity_stft},
    {"mel", parity_mel},
};

static bool read_case(const char *path, void *buf, size_t cap, size_t *len)
{
    return sys_storage_read_file(path, buf, cap, len) == ESP_OK;
}

void app_main(void)
{
    uint8_t *buf = heap_caps_malloc(CASE_BYTES_MAX, MALLOC_CAP_SPIRAM);
    if (sys_storage_init() != ESP_OK || buf == NULL) {
        printf("PARITY error setup\n");
        return;
    }
    unsigned cases = 0;
    for (size_t i = 0; i < sizeof(kBlocks) / sizeof(kBlocks[0]); i++) {
        cases += parity_run_block(GOLDEN_DIR, kBlocks[i].block, kBlocks[i].run, read_case, buf,
                                  CASE_BYTES_MAX, NULL);
    }
    printf("PARITY done %u cases\n", cases);
}
