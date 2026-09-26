#include "sys_storage.h"

#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

#include "esp_littlefs.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_partition.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"
#include "nvs_flash.h"

#define LOCK_TIMEOUT_MS 200
#define PATH_MAX_BYTES 64
#define TMP_SUFFIX ".tmp"
#define DEVICE_ID_PREFIX "sr-"
#define MAC_BYTES 6

_Static_assert(STORAGE_NVS_NAME_MAX_BYTES == NVS_KEY_NAME_MAX_SIZE, "storage_format.h follows nvs.h");
_Static_assert(sizeof(esp_partition_mmap_handle_t) == sizeof(uint32_t), "unmap_handle holds an mmap handle");

static const char *TAG = "sys_storage";

static StaticSemaphore_t s_lock_mem;
static SemaphoreHandle_t m_storage;
static bool s_ready;
static uint32_t s_boot_count;

static esp_err_t lock(void)
{
    if (!s_ready) { return ESP_ERR_INVALID_STATE; }
    return xSemaphoreTake(m_storage, pdMS_TO_TICKS(LOCK_TIMEOUT_MS)) == pdTRUE ? ESP_OK : ESP_ERR_TIMEOUT;
}

static void unlock(void)
{
    xSemaphoreGive(m_storage);
}

static esp_err_t nvs_status(esp_err_t err)
{
    return (err == ESP_ERR_NVS_NOT_FOUND || err == ESP_ERR_NVS_TYPE_MISMATCH) ? ESP_ERR_NOT_FOUND : err;
}

static bool int_fits(sys_storage_int_t type, int64_t value)
{
    switch (type) {
    case SYS_STORAGE_U8: return value >= 0 && value <= UINT8_MAX;
    case SYS_STORAGE_I8: return value >= INT8_MIN && value <= INT8_MAX;
    case SYS_STORAGE_U16: return value >= 0 && value <= UINT16_MAX;
    case SYS_STORAGE_U32: return value >= 0 && value <= UINT32_MAX;
    }
    return false;
}

static esp_err_t get_int(const char *ns, const char *key, sys_storage_int_t type, int64_t *out)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open(ns, NVS_READONLY, &handle);
    if (err != ESP_OK) { return nvs_status(err); }
    uint8_t u8 = 0;
    int8_t i8 = 0;
    uint16_t u16 = 0;
    uint32_t u32 = 0;
    switch (type) {
    case SYS_STORAGE_U8:
        err = nvs_get_u8(handle, key, &u8);
        *out = u8;
        break;
    case SYS_STORAGE_I8:
        err = nvs_get_i8(handle, key, &i8);
        *out = i8;
        break;
    case SYS_STORAGE_U16:
        err = nvs_get_u16(handle, key, &u16);
        *out = u16;
        break;
    case SYS_STORAGE_U32:
        err = nvs_get_u32(handle, key, &u32);
        *out = u32;
        break;
    default: err = ESP_ERR_INVALID_ARG;
    }
    nvs_close(handle);
    return nvs_status(err);
}

static esp_err_t set_int(const char *ns, const char *key, sys_storage_int_t type, int64_t value)
{
    nvs_handle_t handle;
    esp_err_t err = nvs_open(ns, NVS_READWRITE, &handle);
    if (err != ESP_OK) { return err; }
    switch (type) {
    case SYS_STORAGE_U8: err = nvs_set_u8(handle, key, (uint8_t)value); break;
    case SYS_STORAGE_I8: err = nvs_set_i8(handle, key, (int8_t)value); break;
    case SYS_STORAGE_U16: err = nvs_set_u16(handle, key, (uint16_t)value); break;
    case SYS_STORAGE_U32: err = nvs_set_u32(handle, key, (uint32_t)value); break;
    default: err = ESP_ERR_INVALID_ARG;
    }
    if (err == ESP_OK) { err = nvs_commit(handle); }
    nvs_close(handle);
    return err;
}

static esp_err_t read_int(const char *ns, const char *key, sys_storage_int_t type, int64_t *out)
{
    if (ns == NULL || key == NULL) { return ESP_ERR_INVALID_ARG; }
    esp_err_t err = lock();
    if (err == ESP_OK) {
        err = get_int(ns, key, type, out);
        unlock();
    }
    return err;
}

static esp_err_t write_int(const char *ns, const char *key, sys_storage_int_t type, int64_t value)
{
    if (ns == NULL || key == NULL) { return ESP_ERR_INVALID_ARG; }
    esp_err_t err = lock();
    if (err == ESP_OK) {
        err = set_int(ns, key, type, value);
        unlock();
    }
    return err;
}

static esp_err_t open_nvs(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "nvs unusable (%s), erasing it", esp_err_to_name(err));
        err = nvs_flash_erase();
        if (err == ESP_OK) { err = nvs_flash_init(); }
    }
    return err;
}

static esp_err_t mount_lfs(void)
{
    const esp_vfs_littlefs_conf_t cfg = {
        .base_path = STORAGE_LFS_MOUNT,
        .partition_label = STORAGE_LFS_LABEL,
        .format_if_mount_failed = true,
    };
    return esp_vfs_littlefs_register(&cfg);
}

static esp_err_t count_boot(void)
{
    int64_t boots = 0;
    if (get_int(STORAGE_NS_SYS, STORAGE_KEY_BOOT_COUNT, SYS_STORAGE_U32, &boots) != ESP_OK) { boots = 0; }
    s_boot_count = (uint32_t)boots + 1;
    return set_int(STORAGE_NS_SYS, STORAGE_KEY_BOOT_COUNT, SYS_STORAGE_U32, s_boot_count);
}

esp_err_t sys_storage_init(void)
{
    if (s_ready) { return ESP_ERR_INVALID_STATE; }
    if (m_storage == NULL) { m_storage = xSemaphoreCreateMutexStatic(&s_lock_mem); }
    esp_err_t err = open_nvs();
    if (err == ESP_OK) { err = mount_lfs(); }
    if (err == ESP_OK) { err = count_boot(); }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "init failed: %s", esp_err_to_name(err));
        return err;
    }
    s_ready = true;
    size_t total = 0, used = 0;
    esp_littlefs_info(STORAGE_LFS_LABEL, &total, &used);
    ESP_LOGI(TAG, "boot %" PRIu32 ", littlefs %u of %u KB used", s_boot_count, (unsigned)(used / 1024),
             (unsigned)(total / 1024));
    return ESP_OK;
}

esp_err_t sys_storage_get_str(const char *ns, const char *key, char *out, size_t cap)
{
    if (ns == NULL || key == NULL || out == NULL || cap == 0) { return ESP_ERR_INVALID_ARG; }
    out[0] = '\0';
    esp_err_t err = lock();
    if (err != ESP_OK) { return err; }
    nvs_handle_t handle;
    err = nvs_open(ns, NVS_READONLY, &handle);
    if (err == ESP_OK) {
        size_t len = cap;
        err = nvs_get_str(handle, key, out, &len);
        nvs_close(handle);
    }
    unlock();
    if (err == ESP_ERR_NVS_INVALID_LENGTH) {
        out[0] = '\0';
        return ESP_ERR_INVALID_SIZE;
    }
    if (err == ESP_OK && out[0] == '\0') { return ESP_ERR_NOT_FOUND; }
    return nvs_status(err);
}

esp_err_t sys_storage_set_str(const char *ns, const char *key, const char *value)
{
    if (ns == NULL || key == NULL || value == NULL || value[0] == '\0') { return ESP_ERR_INVALID_ARG; }
    esp_err_t err = lock();
    if (err != ESP_OK) { return err; }
    nvs_handle_t handle;
    err = nvs_open(ns, NVS_READWRITE, &handle);
    if (err == ESP_OK) {
        err = nvs_set_str(handle, key, value);
        if (err == ESP_OK) { err = nvs_commit(handle); }
        nvs_close(handle);
    }
    unlock();
    return err;
}

esp_err_t sys_storage_get_u8(const char *ns, const char *key, uint8_t *out)
{
    int64_t value = 0;
    const esp_err_t err = out != NULL ? read_int(ns, key, SYS_STORAGE_U8, &value) : ESP_ERR_INVALID_ARG;
    if (err == ESP_OK) { *out = (uint8_t)value; }
    return err;
}

esp_err_t sys_storage_get_i8(const char *ns, const char *key, int8_t *out)
{
    int64_t value = 0;
    const esp_err_t err = out != NULL ? read_int(ns, key, SYS_STORAGE_I8, &value) : ESP_ERR_INVALID_ARG;
    if (err == ESP_OK) { *out = (int8_t)value; }
    return err;
}

esp_err_t sys_storage_get_u16(const char *ns, const char *key, uint16_t *out)
{
    int64_t value = 0;
    const esp_err_t err = out != NULL ? read_int(ns, key, SYS_STORAGE_U16, &value) : ESP_ERR_INVALID_ARG;
    if (err == ESP_OK) { *out = (uint16_t)value; }
    return err;
}

esp_err_t sys_storage_get_u32(const char *ns, const char *key, uint32_t *out)
{
    int64_t value = 0;
    const esp_err_t err = out != NULL ? read_int(ns, key, SYS_STORAGE_U32, &value) : ESP_ERR_INVALID_ARG;
    if (err == ESP_OK) { *out = (uint32_t)value; }
    return err;
}

esp_err_t sys_storage_set_u8(const char *ns, const char *key, uint8_t value)
{
    return write_int(ns, key, SYS_STORAGE_U8, value);
}

esp_err_t sys_storage_set_i8(const char *ns, const char *key, int8_t value)
{
    return write_int(ns, key, SYS_STORAGE_I8, value);
}

esp_err_t sys_storage_set_u16(const char *ns, const char *key, uint16_t value)
{
    return write_int(ns, key, SYS_STORAGE_U16, value);
}

esp_err_t sys_storage_set_u32(const char *ns, const char *key, uint32_t value)
{
    return write_int(ns, key, SYS_STORAGE_U32, value);
}

esp_err_t sys_storage_get_blob(const char *ns, const char *key, void *out, size_t bytes)
{
    if (ns == NULL || key == NULL || out == NULL || bytes == 0) { return ESP_ERR_INVALID_ARG; }
    esp_err_t err = lock();
    if (err != ESP_OK) { return err; }
    nvs_handle_t handle;
    err = nvs_open(ns, NVS_READONLY, &handle);
    if (err == ESP_OK) {
        size_t stored = 0;
        err = nvs_get_blob(handle, key, NULL, &stored);
        if (err == ESP_OK && stored != bytes) { err = ESP_ERR_INVALID_SIZE; }
        if (err == ESP_OK) { err = nvs_get_blob(handle, key, out, &stored); }
        nvs_close(handle);
    }
    unlock();
    return nvs_status(err);
}

esp_err_t sys_storage_set_blob(const char *ns, const char *key, const void *data, size_t bytes)
{
    if (ns == NULL || key == NULL || data == NULL || bytes == 0) { return ESP_ERR_INVALID_ARG; }
    esp_err_t err = lock();
    if (err != ESP_OK) { return err; }
    nvs_handle_t handle;
    err = nvs_open(ns, NVS_READWRITE, &handle);
    if (err == ESP_OK) {
        err = nvs_set_blob(handle, key, data, bytes);
        if (err == ESP_OK) { err = nvs_commit(handle); }
        nvs_close(handle);
    }
    unlock();
    return err;
}

esp_err_t sys_storage_erase(const char *ns, const char *key)
{
    if (ns == NULL || key == NULL) { return ESP_ERR_INVALID_ARG; }
    esp_err_t err = lock();
    if (err != ESP_OK) { return err; }
    nvs_handle_t handle;
    err = nvs_open(ns, NVS_READWRITE, &handle);
    if (err == ESP_OK) {
        err = nvs_erase_key(handle, key);
        if (err == ESP_ERR_NVS_NOT_FOUND) { err = ESP_OK; }
        if (err == ESP_OK) { err = nvs_commit(handle); }
        nvs_close(handle);
    }
    unlock();
    return err;
}

esp_err_t sys_storage_seed(uint32_t seed_ver, const sys_storage_seed_t *seeds, size_t count)
{
    if (seeds == NULL && count > 0) { return ESP_ERR_INVALID_ARG; }
    for (size_t i = 0; i < count; i++) {
        if (seeds[i].ns == NULL || seeds[i].key == NULL || !int_fits(seeds[i].type, seeds[i].value)) {
            return ESP_ERR_INVALID_ARG;
        }
    }
    esp_err_t err = lock();
    if (err != ESP_OK) { return err; }
    int64_t device_ver = 0;
    const bool stale =
        get_int(STORAGE_NS_SYS, STORAGE_KEY_SEED_VER, SYS_STORAGE_U32, &device_ver) != ESP_OK ||
        device_ver < seed_ver;
    size_t written = 0;
    for (size_t i = 0; i < count && err == ESP_OK; i++) {
        int64_t stored = 0;
        const bool present = get_int(seeds[i].ns, seeds[i].key, seeds[i].type, &stored) == ESP_OK;
        // A value SET_CONFIG wrote wins until a newer seed set reaches the device (KEHOACH 6.2).
        if (present && (!stale || stored == seeds[i].value)) { continue; }
        err = set_int(seeds[i].ns, seeds[i].key, seeds[i].type, seeds[i].value);
        written += err == ESP_OK ? 1 : 0;
    }
    if (err == ESP_OK && stale) {
        err = set_int(STORAGE_NS_SYS, STORAGE_KEY_SEED_VER, SYS_STORAGE_U32, seed_ver);
    }
    unlock();
    ESP_LOGI(TAG, "seed set %" PRIu32 "%s: %u of %u keys written, %s", seed_ver, stale ? " (newer)" : "",
             (unsigned)written, (unsigned)count, esp_err_to_name(err));
    return err;
}

uint32_t sys_storage_boot_count(void)
{
    return s_boot_count;
}

esp_err_t sys_storage_device_id(char *out, size_t cap)
{
    if (out == NULL || cap == 0) { return ESP_ERR_INVALID_ARG; }
    const esp_err_t err = sys_storage_get_str(STORAGE_NS_DEVICE, STORAGE_KEY_SERIAL, out, cap);
    if (err != ESP_ERR_NOT_FOUND) { return err; }
    uint8_t mac[MAC_BYTES];
    // The factory MAC sits in eFuse, so erasing the flash keeps the deviceId (KEHOACH 6.2).
    if (esp_efuse_mac_get_default(mac) != ESP_OK) { return ESP_FAIL; }
    const int len = snprintf(out, cap, DEVICE_ID_PREFIX "%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2],
                             mac[3], mac[4], mac[5]);
    if (len < 0 || (size_t)len >= cap) {
        out[0] = '\0';
        return ESP_ERR_INVALID_SIZE;
    }
    return ESP_OK;
}

static bool on_lfs(const char *path)
{
    return strncmp(path, STORAGE_LFS_MOUNT "/", sizeof(STORAGE_LFS_MOUNT)) == 0;
}

static esp_err_t read_locked(const char *path, void *buf, size_t cap, size_t *len)
{
    struct stat st;
    if (stat(path, &st) != 0) { return ESP_ERR_NOT_FOUND; }
    if ((size_t)st.st_size > cap) { return ESP_ERR_INVALID_SIZE; }
    FILE *file = fopen(path, "rb");
    if (file == NULL) { return ESP_ERR_NOT_FOUND; }
    const size_t got = fread(buf, 1, (size_t)st.st_size, file);
    fclose(file);
    *len = got;
    return got == (size_t)st.st_size ? ESP_OK : ESP_FAIL;
}

esp_err_t sys_storage_read_file(const char *path, void *buf, size_t cap, size_t *len)
{
    if (path == NULL || buf == NULL || len == NULL || !on_lfs(path)) { return ESP_ERR_INVALID_ARG; }
    *len = 0;
    esp_err_t err = lock();
    if (err == ESP_OK) {
        err = read_locked(path, buf, cap, len);
        unlock();
    }
    return err;
}

static esp_err_t make_parents(char *path)
{
    for (char *at = path + sizeof(STORAGE_LFS_MOUNT); *at != '\0'; at++) {
        if (*at != '/') { continue; }
        *at = '\0';
        const bool made = mkdir(path, 0775) == 0 || errno == EEXIST;
        *at = '/';
        if (!made) { return ESP_FAIL; }
    }
    return ESP_OK;
}

static esp_err_t write_whole(const char *path, const void *data, size_t len)
{
    FILE *file = fopen(path, "wb");
    if (file == NULL) { return ESP_FAIL; }
    const size_t written = len > 0 ? fwrite(data, 1, len, file) : 0;
    const int closed = fclose(file);
    return (written == len && closed == 0) ? ESP_OK : ESP_FAIL;
}

static esp_err_t replace_locked(const char *path, const void *data, size_t len)
{
    char tmp[PATH_MAX_BYTES];
    const int n = snprintf(tmp, sizeof(tmp), "%s" TMP_SUFFIX, path);
    if (n < 0 || (size_t)n >= sizeof(tmp)) { return ESP_ERR_INVALID_SIZE; }
    esp_err_t err = make_parents(tmp);
    if (err == ESP_OK) { err = write_whole(tmp, data, len); }
    // lfs_rename replaces the target in one commit: a cut leaves old or new, whole (KEHOACH 6.4).
    if (err == ESP_OK && rename(tmp, path) != 0) { err = ESP_FAIL; }
    if (err != ESP_OK) { remove(tmp); }
    return err;
}

esp_err_t sys_storage_write_file(const char *path, const void *data, size_t len)
{
    if (path == NULL || (data == NULL && len > 0) || !on_lfs(path)) { return ESP_ERR_INVALID_ARG; }
    esp_err_t err = lock();
    if (err == ESP_OK) {
        err = replace_locked(path, data, len);
        unlock();
    }
    return err;
}

static esp_err_t check_models(const storage_model_header_t *header, size_t partition_bytes)
{
    // Erased flash reads all ones: a slot never written, not a damaged image.
    if (header->magic != STORAGE_MODEL_MAGIC) { return ESP_ERR_NOT_FOUND; }
    if (header->format_ver != STORAGE_MODEL_FORMAT_VER) { return ESP_ERR_INVALID_VERSION; }
    if (header->count > STORAGE_MODEL_MAX_ENTRIES) { return ESP_ERR_INVALID_SIZE; }
    for (uint32_t i = 0; i < header->count; i++) {
        const storage_model_entry_t *entry = &header->entry[i];
        const bool inside = entry->offset >= STORAGE_MODEL_HEADER_BYTES && entry->size > 0 &&
                            (uint64_t)entry->offset + entry->size <= partition_bytes;
        if (!inside || entry->offset % STORAGE_MODEL_ALIGN_BYTES != 0) { return ESP_ERR_INVALID_SIZE; }
    }
    return ESP_OK;
}

esp_err_t sys_storage_map_models(uint8_t slot, sys_storage_models_t *out)
{
    if (out == NULL || slot > 1) { return ESP_ERR_INVALID_ARG; }
    if (!s_ready) { return ESP_ERR_INVALID_STATE; }
    const char *label = slot == 0 ? STORAGE_MODEL_LABEL_SLOT0 : STORAGE_MODEL_LABEL_SLOT1;
    const esp_partition_t *part =
        esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, label);
    if (part == NULL) { return ESP_ERR_NOT_FOUND; }
    if (part->size < STORAGE_MODEL_HEADER_BYTES) { return ESP_ERR_INVALID_SIZE; }
    const void *base = NULL;
    esp_partition_mmap_handle_t handle;
    esp_err_t err = esp_partition_mmap(part, 0, part->size, ESP_PARTITION_MMAP_DATA, &base, &handle);
    if (err != ESP_OK) { return err; }
    err = check_models(base, part->size);
    if (err != ESP_OK) {
        esp_partition_munmap(handle);
        return err;
    }
    out->header = base;
    out->partition_bytes = part->size;
    out->unmap_handle = handle;
    return ESP_OK;
}

void sys_storage_unmap_models(sys_storage_models_t *models)
{
    if (models == NULL || models->header == NULL) { return; }
    esp_partition_munmap(models->unmap_handle);
    memset(models, 0, sizeof(*models));
}
