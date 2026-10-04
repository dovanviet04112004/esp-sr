/** The only door to NVS, LittleFS and raw partitions (CLAUDE.md 4.1, KEHOACH 6.2).
 *  Every NVS and file call waits at most 200 ms for m_storage, a leaf lock (KEHOACH 5.3); none may run on
 * core 1.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "storage_format.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    SYS_STORAGE_U8,
    SYS_STORAGE_I8,
    SYS_STORAGE_U16,
    SYS_STORAGE_U32,
} sys_storage_int_t;

typedef struct {
    const char *ns;
    const char *key;
    sys_storage_int_t type;
    int64_t value; // must fit type
} sys_storage_seed_t;

typedef struct {
    const storage_model_header_t *header; // entries validated against partition_bytes
    size_t partition_bytes;
    uint32_t unmap_handle;
} sys_storage_models_t;

/** Initialise NVS, mount LittleFS (formatting it when unreadable) and count this boot.
 *  @ctx task | blocking | once at boot, ahead of every other call
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE on a second call | an nvs_flash or littlefs error
 */
esp_err_t sys_storage_init(void);

/** Read a string; a key holding "" counts as absent (KEHOACH 6.2).
 *  @ctx task | blocking | caller owns out
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND absent or empty | ESP_ERR_INVALID_SIZE cap too short | ESP_ERR_TIMEOUT
 */
esp_err_t sys_storage_get_str(const char *ns, const char *key, char *out, size_t cap);

/** Write a string; "" is refused, sys_storage_erase drops a key.
 *  @ctx task | blocking, writes flash
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG empty value | ESP_ERR_TIMEOUT | an nvs error
 */
esp_err_t sys_storage_set_str(const char *ns, const char *key, const char *value);

/** Read an integer stored with exactly this type; a key of another type counts as absent.
 *  @ctx task | blocking
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND | ESP_ERR_TIMEOUT
 */
esp_err_t sys_storage_get_u8(const char *ns, const char *key, uint8_t *out);

/** Same as sys_storage_get_u8 for an i8 key.
 *  @ctx task | blocking
 */
esp_err_t sys_storage_get_i8(const char *ns, const char *key, int8_t *out);

/** Same as sys_storage_get_u8 for a u16 key.
 *  @ctx task | blocking
 */
esp_err_t sys_storage_get_u16(const char *ns, const char *key, uint16_t *out);

/** Same as sys_storage_get_u8 for a u32 key.
 *  @ctx task | blocking
 */
esp_err_t sys_storage_get_u32(const char *ns, const char *key, uint32_t *out);

/** Write an NVS u8; each key takes the setter of the type storage_format.h gives it.
 *  @ctx task | blocking, writes flash
 *  @ret ESP_OK | ESP_ERR_TIMEOUT | an nvs error
 */
esp_err_t sys_storage_set_u8(const char *ns, const char *key, uint8_t value);

/** Same as sys_storage_set_u8 for an i8 key.
 *  @ctx task | blocking, writes flash
 */
esp_err_t sys_storage_set_i8(const char *ns, const char *key, int8_t value);

/** Same as sys_storage_set_u8 for a u16 key.
 *  @ctx task | blocking, writes flash
 */
esp_err_t sys_storage_set_u16(const char *ns, const char *key, uint16_t value);

/** Same as sys_storage_set_u8 for a u32 key.
 *  @ctx task | blocking, writes flash
 */
esp_err_t sys_storage_set_u32(const char *ns, const char *key, uint32_t value);

/** Read a blob that must be exactly bytes long.
 *  @ctx task | blocking | caller owns out
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND | ESP_ERR_INVALID_SIZE stored length differs | ESP_ERR_TIMEOUT
 */
esp_err_t sys_storage_get_blob(const char *ns, const char *key, void *out, size_t bytes);

/** Write a blob.
 *  @ctx task | blocking, writes flash
 */
esp_err_t sys_storage_set_blob(const char *ns, const char *key, const void *data, size_t bytes);

/** Drop one key; dropping an absent key is not an error.
 *  @ctx task | blocking, writes flash
 */
esp_err_t sys_storage_erase(const char *ns, const char *key);

/** Write absent seeds, and every seed that differs while sys/seed_ver is below seed_ver (KEHOACH 6.2).
 *  @ctx task | blocking, writes flash | at boot, ahead of any reader of these keys
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG a value outside its type | the first write error, seed_ver kept
 */
esp_err_t sys_storage_seed(uint32_t seed_ver, const sys_storage_seed_t *seeds, size_t count);

/** Boots counted in NVS sys/boot_count, this one included.
 *  @ctx any | non-blocking | 0 until sys_storage_init succeeds
 */
uint32_t sys_storage_boot_count(void);

/** deviceId: NVS device/serial when set, else "sr-" and the factory MAC as 12 lower-case hex digits.
 *  @ctx task | blocking | caller owns out; cap 16 holds the MAC form
 *  @ret ESP_OK | ESP_ERR_INVALID_SIZE the id does not fit cap | ESP_ERR_TIMEOUT
 */
esp_err_t sys_storage_device_id(char *out, size_t cap);

/** Read a whole LittleFS file into buf.
 *  @ctx task | blocking, reads flash | caller owns buf
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND | ESP_ERR_INVALID_SIZE file larger than cap | ESP_FAIL
 */
esp_err_t sys_storage_read_file(const char *path, void *buf, size_t cap, size_t *len);

/** Replace a LittleFS file through path.tmp and one rename, creating parent directories (KEHOACH 6.4).
 *  @ctx task | blocking, writes flash | a power cut leaves the old file or the new one, whole
 *  @ret ESP_OK | ESP_ERR_INVALID_SIZE path too long | ESP_FAIL
 */
esp_err_t sys_storage_write_file(const char *path, const void *data, size_t len);

/** Memory-map models_0, slot 0, or a test table's models_1, slot 1, read-only; check its header layout.
 *  @ctx task | blocking | the mapping stays valid until sys_storage_unmap_models
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND no image or no partition | ESP_ERR_INVALID_VERSION
 *       | ESP_ERR_INVALID_SIZE bad entry
 */
esp_err_t sys_storage_map_models(uint8_t slot, sys_storage_models_t *out);

/** Release a mapping from sys_storage_map_models.
 *  @ctx task | non-blocking
 */
void sys_storage_unmap_models(sys_storage_models_t *models);

#ifdef __cplusplus
}
#endif
