#include <dirent.h>
#include <stdio.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "parity.h"
#include "sys_storage.h"

#define GOLDEN_DIR STORAGE_LFS_MOUNT "/golden"
#define CASE_BYTES_MAX (128 * 1024)
#define DIR_BYTES 64
#define NAME_BYTES 256 // struct dirent d_name
#define PATH_BYTES (DIR_BYTES + 1 + NAME_BYTES)
#define GOLD_SUFFIX ".gold"

typedef bool (*parity_runner_t)(const char *case_name, const void *buf, size_t len);

static const struct {
    const char *block;
    parity_runner_t run;
} kBlocks[] = {
    {"stft", parity_stft},
    {"mel", parity_mel},
};

static unsigned run_block(const char *block, parity_runner_t run, uint8_t *buf)
{
    char dir_path[DIR_BYTES];
    snprintf(dir_path, sizeof(dir_path), GOLDEN_DIR "/%s", block);
    DIR *dir = opendir(dir_path);
    if (dir == NULL) {
        printf("PARITY missing %s\n", dir_path);
        return 0;
    }
    unsigned cases = 0;
    for (struct dirent *entry = readdir(dir); entry != NULL; entry = readdir(dir)) {
        const size_t name_len = strlen(entry->d_name);
        const size_t suffix_len = strlen(GOLD_SUFFIX);
        if (name_len <= suffix_len || strcmp(entry->d_name + name_len - suffix_len, GOLD_SUFFIX) != 0) {
            continue;
        }
        char path[PATH_BYTES];
        char case_name[NAME_BYTES];
        snprintf(path, sizeof(path), "%s/%s", dir_path, entry->d_name);
        snprintf(case_name, sizeof(case_name), "%.*s", (int)(name_len - suffix_len), entry->d_name);
        size_t len = 0;
        if (sys_storage_read_file(path, buf, CASE_BYTES_MAX, &len) != ESP_OK || !run(case_name, buf, len)) {
            printf("PARITY error %s %s\n", block, case_name);
            continue;
        }
        cases++;
    }
    closedir(dir);
    return cases;
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
        cases += run_block(kBlocks[i].block, kBlocks[i].run, buf);
    }
    printf("PARITY done %u cases\n", cases);
}
