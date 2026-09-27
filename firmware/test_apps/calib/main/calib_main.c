#include <inttypes.h>
#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_check.h"
#include "esp_console.h"
#include "esp_log.h"
#include "drv_audio.h"
#include "esp_rom_crc.h"
#include "gen_grid.h"
#include "storage_format.h"
#include "sys_storage.h"

#define PROMPT "calib> "
#define CMDLINE_BYTES 256
#define REPL_STACK_BYTES 4096
#define VALUES_PER_BIN 2 // re, im as storage_format.h lays out calib/bal

static const char *TAG = "calib";

static float s_staged[GEN_GRID_N_BINS * VALUES_PER_BIN];
static bool s_bin_set[GEN_GRID_N_BINS];
static float s_read_back[GEN_GRID_N_BINS * VALUES_PER_BIN];

static bool parse_u32(const char *text, uint32_t *out)
{
    char *end = NULL;
    const unsigned long value = strtoul(text, &end, 10);
    if (end == text || *end != '\0' || value > UINT32_MAX) { return false; }
    *out = (uint32_t)value;
    return true;
}

static int bal_clear(void)
{
    memset(s_staged, 0, sizeof(s_staged));
    memset(s_bin_set, 0, sizeof(s_bin_set));
    printf("ok cleared\n");
    return 0;
}

static int bal_put(int argc, char **argv)
{
    uint32_t first = 0;
    const int values = argc - 3;
    if (argc < 5 || !parse_u32(argv[2], &first) || values % VALUES_PER_BIN != 0) {
        printf("error usage: bal put <first_bin> <re> <im> [<re> <im> ...]\n");
        return 1;
    }
    const uint32_t bins = (uint32_t)(values / VALUES_PER_BIN);
    if (first + bins > GEN_GRID_N_BINS) {
        printf("error bins %" PRIu32 "..%" PRIu32 " out of range 0..%d\n", first, first + bins - 1,
               GEN_GRID_N_BINS - 1);
        return 1;
    }
    float parsed[CMDLINE_BYTES / 2];
    for (int i = 0; i < values; i++) {
        char *end = NULL;
        parsed[i] = strtof(argv[3 + i], &end);
        if (end == argv[3 + i] || *end != '\0' || !isfinite(parsed[i])) {
            printf("error value '%s' is not a finite float\n", argv[3 + i]);
            return 1;
        }
    }
    memcpy(&s_staged[first * VALUES_PER_BIN], parsed, (size_t)values * sizeof(float));
    for (uint32_t k = first; k < first + bins; k++) {
        s_bin_set[k] = true;
    }
    printf("ok put %" PRIu32 "..%" PRIu32 "\n", first, first + bins - 1);
    return 0;
}

static int bal_commit(int argc, char **argv)
{
    uint32_t at_s = 0;
    if (argc != 3 || !parse_u32(argv[2], &at_s)) {
        printf("error usage: bal commit <unix_seconds>\n");
        return 1;
    }
    int missing = 0;
    for (int k = 0; k < GEN_GRID_N_BINS; k++) {
        missing += s_bin_set[k] ? 0 : 1;
    }
    if (missing > 0) {
        printf("error %d of %d bins not put\n", missing, GEN_GRID_N_BINS);
        return 1;
    }
    esp_err_t err =
        sys_storage_set_blob(STORAGE_NS_CALIB, STORAGE_KEY_BAL, s_staged, STORAGE_CALIB_BAL_BYTES);
    if (err == ESP_OK) {
        err = sys_storage_set_u32(STORAGE_NS_CALIB, STORAGE_KEY_BAL_VER, STORAGE_CALIB_BAL_VERSION);
    }
    if (err == ESP_OK) { err = sys_storage_set_u32(STORAGE_NS_CALIB, STORAGE_KEY_BAL_AT, at_s); }
    if (err != ESP_OK) {
        printf("error writing calib/bal: %s\n", esp_err_to_name(err));
        return 1;
    }
    printf("ok committed ver %d at %" PRIu32 "\n", STORAGE_CALIB_BAL_VERSION, at_s);
    return 0;
}

static int bal_show(void)
{
    uint32_t ver = 0;
    uint32_t at_s = 0;
    esp_err_t err =
        sys_storage_get_blob(STORAGE_NS_CALIB, STORAGE_KEY_BAL, s_read_back, STORAGE_CALIB_BAL_BYTES);
    if (err == ESP_OK) { err = sys_storage_get_u32(STORAGE_NS_CALIB, STORAGE_KEY_BAL_VER, &ver); }
    if (err == ESP_OK) { err = sys_storage_get_u32(STORAGE_NS_CALIB, STORAGE_KEY_BAL_AT, &at_s); }
    if (err != ESP_OK) {
        printf("error no calib/bal: %s\n", esp_err_to_name(err));
        return 1;
    }
    const uint32_t crc = esp_rom_crc32_le(0, (const uint8_t *)s_read_back, STORAGE_CALIB_BAL_BYTES);
    printf("ok bal ver %" PRIu32 " at %" PRIu32 " crc 0x%08" PRIx32 " bins %d\n", ver, at_s, crc,
           GEN_GRID_N_BINS);
    return 0;
}

static int bal_cmd(int argc, char **argv)
{
    const char *action = argc > 1 ? argv[1] : "";
    if (strcmp(action, "clear") == 0) { return bal_clear(); }
    if (strcmp(action, "put") == 0) { return bal_put(argc, argv); }
    if (strcmp(action, "commit") == 0) { return bal_commit(argc, argv); }
    if (strcmp(action, "show") == 0) { return bal_show(); }
    printf("error usage: bal clear | put <first_bin> <re> <im>... | commit <unix_seconds> | show\n");
    return 1;
}

static int shift_cmd(int argc, char **argv)
{
    const char *action = argc > 1 ? argv[1] : "";
    uint32_t shift = 0;
    if (strcmp(action, "set") == 0 && argc == 3 && parse_u32(argv[2], &shift) &&
        shift >= DRV_AUDIO_PCM_SHIFT_MIN && shift <= DRV_AUDIO_PCM_SHIFT_MAX) {
        const esp_err_t err = sys_storage_set_u8(STORAGE_NS_CALIB, STORAGE_KEY_PCM_SHIFT, (uint8_t)shift);
        if (err != ESP_OK) {
            printf("error writing calib/pcm_shift: %s\n", esp_err_to_name(err));
            return 1;
        }
    } else if (strcmp(action, "show") != 0) {
        printf("error usage: shift set <%d..%d> | show\n", DRV_AUDIO_PCM_SHIFT_MIN, DRV_AUDIO_PCM_SHIFT_MAX);
        return 1;
    }
    uint8_t stored = 0;
    const esp_err_t err = sys_storage_get_u8(STORAGE_NS_CALIB, STORAGE_KEY_PCM_SHIFT, &stored);
    if (err != ESP_OK) {
        printf("error no calib/pcm_shift: %s\n", esp_err_to_name(err));
        return 1;
    }
    printf("ok shift %u\n", stored);
    return 0;
}

void app_main(void)
{
    ESP_ERROR_CHECK(sys_storage_init());
    esp_console_repl_config_t config = ESP_CONSOLE_REPL_CONFIG_DEFAULT();
    config.prompt = PROMPT;
    config.max_cmdline_length = CMDLINE_BYTES;
    config.task_stack_size = REPL_STACK_BYTES;
    config.history_save_path = NULL;
    const esp_console_dev_uart_config_t uart = ESP_CONSOLE_DEV_UART_CONFIG_DEFAULT();
    esp_console_repl_t *repl = NULL;
    ESP_ERROR_CHECK(esp_console_new_repl_uart(&uart, &config, &repl));
    const esp_console_cmd_t bal = {
        .command = "bal",
        .help = "Stage, commit and read back NVS calib/bal (KEHOACH 3.4); host/srhost/calib.py drives it",
        .func = bal_cmd,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&bal));
    const esp_console_cmd_t shift = {
        .command = "shift",
        .help = "Store and read back NVS calib/pcm_shift, the right shift drv_audio applies (E2-T5)",
        .func = shift_cmd,
    };
    ESP_ERROR_CHECK(esp_console_cmd_register(&shift));
    ESP_ERROR_CHECK(esp_console_start_repl(repl));
    ESP_LOGI(TAG, "ready: %d bins of %d bytes each", GEN_GRID_N_BINS, (int)(VALUES_PER_BIN * sizeof(float)));
}
