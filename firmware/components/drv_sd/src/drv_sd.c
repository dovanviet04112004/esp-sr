#include "drv_sd.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

#include "app_config.h"
#include "driver/sdspi_host.h"
#include "driver/spi_common.h"
#include "esp_log.h"
#include "esp_vfs_fat.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "sdmmc_cmd.h"

#define MAX_OPEN_FILES 1
#define ALLOCATION_UNIT_BYTES (16 * 1024)
#define SPI_TRANSFER_MAX_BYTES 4000
#define PATH_MAX_BYTES 64
#define LOCK_TIMEOUT_MS 10000 // a full picture's write holds it longest (KEHOACH 5.3)

static const char *TAG = "drv_sd";

static sdmmc_card_t *s_card;
static bool s_tried;
static SemaphoreHandle_t m_sd; // nhin_task and ghi_task share the card
static StaticSemaphore_t s_lock_mem;

esp_err_t drv_sd_init(void)
{
    if (s_tried) { return ESP_ERR_INVALID_STATE; }
    s_tried = true;
    m_sd = xSemaphoreCreateMutexStatic(&s_lock_mem);
    const spi_bus_config_t bus = {
        .mosi_io_num = APP_SD_MOSI_GPIO,
        .miso_io_num = APP_SD_MISO_GPIO,
        .sclk_io_num = APP_SD_CLK_GPIO,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = SPI_TRANSFER_MAX_BYTES,
    };
    esp_err_t err = spi_bus_initialize(SPI2_HOST, &bus, SDSPI_DEFAULT_DMA);
    if (err != ESP_OK) { return err; }
    const sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    sdspi_device_config_t slot = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot.gpio_cs = APP_SD_CS_GPIO;
    slot.host_id = SPI2_HOST;
    const esp_vfs_fat_mount_config_t mount = {
        .format_if_mount_failed = false,
        .max_files = MAX_OPEN_FILES,
        .allocation_unit_size = ALLOCATION_UNIT_BYTES,
    };
    err = esp_vfs_fat_sdspi_mount(DRV_SD_ROOT, &host, &slot, &mount, &s_card);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "no card: %s", esp_err_to_name(err));
        s_card = NULL;
        spi_bus_free(SPI2_HOST);
        return ESP_ERR_NOT_FOUND;
    }
    ESP_LOGI(TAG, "card %s, %llu MB", s_card->cid.name,
             (unsigned long long)s_card->csd.capacity * s_card->csd.sector_size / (1024 * 1024));
    return ESP_OK;
}

bool drv_sd_ready(void)
{
    return s_card != NULL;
}

static esp_err_t make_folders(char *full)
{
    for (char *slash = strchr(full + sizeof(DRV_SD_ROOT), '/'); slash != NULL;
         slash = strchr(slash + 1, '/')) {
        *slash = '\0';
        const int made = mkdir(full, 0775);
        const int reason = errno;
        *slash = '/';
        if (made != 0 && reason != EEXIST) { return ESP_FAIL; }
    }
    return ESP_OK;
}

static esp_err_t take_card(void)
{
    if (s_card == NULL) { return ESP_ERR_INVALID_STATE; }
    return xSemaphoreTake(m_sd, pdMS_TO_TICKS(LOCK_TIMEOUT_MS)) == pdTRUE ? ESP_OK : ESP_ERR_TIMEOUT;
}

static void give_card(void)
{
    xSemaphoreGive(m_sd);
}

static bool fits(int length, size_t bytes)
{
    return length > 0 && (size_t)length < bytes;
}

static bool exists_held(const char *path)
{
    char full[PATH_MAX_BYTES];
    const int length = snprintf(full, sizeof(full), DRV_SD_ROOT "/%s", path);
    struct stat info;
    return fits(length, sizeof(full)) && stat(full, &info) == 0;
}

bool drv_sd_exists(const char *path)
{
    if (take_card() != ESP_OK) { return false; }
    const bool there = exists_held(path);
    give_card();
    return there;
}

esp_err_t drv_sd_name(const char *stem, const char *extension, char *path, size_t path_bytes)
{
    const esp_err_t taken = take_card();
    if (taken != ESP_OK) { return taken; }
    int length = snprintf(path, path_bytes, "%s.%s", stem, extension);
    for (unsigned copy = 1; copy <= DRV_SD_NAME_COPIES_MAX && fits(length, path_bytes) && exists_held(path);
         copy++) {
        length = snprintf(path, path_bytes, "%s_%u.%s", stem, copy, extension);
    }
    give_card();
    return fits(length, path_bytes) ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

static esp_err_t write_held(const char *path, const uint8_t *data, size_t bytes)
{
    char full[PATH_MAX_BYTES];
    const int length = snprintf(full, sizeof(full), DRV_SD_ROOT "/%s", path);
    if (!fits(length, sizeof(full))) { return ESP_ERR_INVALID_SIZE; }
    if (make_folders(full) != ESP_OK) { return ESP_FAIL; }
    FILE *file = fopen(full, "wb");
    if (file == NULL) { return ESP_FAIL; }
    const size_t wrote = fwrite(data, 1, bytes, file);
    const int closed = fclose(file);
    if (wrote == bytes && closed == 0) { return ESP_OK; }
    remove(full);
    return ESP_FAIL;
}

esp_err_t drv_sd_write(const char *path, const uint8_t *data, size_t bytes)
{
    esp_err_t err = take_card();
    if (err != ESP_OK) { return err; }
    err = write_held(path, data, bytes);
    give_card();
    return err;
}

static esp_err_t open_full(const char *path, const char *mode, FILE **file)
{
    char full[PATH_MAX_BYTES];
    const int length = snprintf(full, sizeof(full), DRV_SD_ROOT "/%s", path);
    if (!fits(length, sizeof(full))) { return ESP_ERR_INVALID_SIZE; }
    *file = fopen(full, mode);
    return *file != NULL ? ESP_OK : ESP_FAIL;
}

static esp_err_t append_held(const char *path, const uint8_t *data, size_t bytes)
{
    FILE *file = NULL;
    const esp_err_t err = open_full(path, "ab", &file);
    if (err != ESP_OK) { return err; }
    const size_t wrote = fwrite(data, 1, bytes, file);
    const int closed = fclose(file);
    return wrote == bytes && closed == 0 ? ESP_OK : ESP_FAIL;
}

esp_err_t drv_sd_append(const char *path, const uint8_t *data, size_t bytes)
{
    esp_err_t err = take_card();
    if (err != ESP_OK) { return err; }
    err = append_held(path, data, bytes);
    give_card();
    return err;
}

static esp_err_t patch_held(const char *path, size_t offset, const uint8_t *data, size_t bytes)
{
    FILE *file = NULL;
    const esp_err_t err = open_full(path, "r+b", &file);
    if (err != ESP_OK) { return err; }
    const bool wrote = fseek(file, (long)offset, SEEK_SET) == 0 && fwrite(data, 1, bytes, file) == bytes;
    const int closed = fclose(file);
    return wrote && closed == 0 ? ESP_OK : ESP_FAIL;
}

esp_err_t drv_sd_patch(const char *path, size_t offset, const uint8_t *data, size_t bytes)
{
    esp_err_t err = take_card();
    if (err != ESP_OK) { return err; }
    err = patch_held(path, offset, data, bytes);
    give_card();
    return err;
}

static esp_err_t read_held(const char *path, size_t offset, uint8_t *data, size_t bytes, size_t *got)
{
    FILE *file = NULL;
    const esp_err_t err = open_full(path, "rb", &file);
    if (err != ESP_OK) { return err; }
    const bool placed = fseek(file, (long)offset, SEEK_SET) == 0;
    if (placed) { *got = fread(data, 1, bytes, file); }
    const bool failed = !placed || ferror(file) != 0;
    fclose(file);
    return failed ? ESP_FAIL : ESP_OK;
}

esp_err_t drv_sd_read(const char *path, size_t offset, uint8_t *data, size_t bytes, size_t *got)
{
    *got = 0;
    esp_err_t err = take_card();
    if (err != ESP_OK) { return err; }
    err = read_held(path, offset, data, bytes, got);
    give_card();
    return err;
}
