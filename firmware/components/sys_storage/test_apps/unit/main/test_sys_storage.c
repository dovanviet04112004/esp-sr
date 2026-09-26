#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "driver/gptimer.h"
#include "esp_attr.h"
#include "esp_err.h"
#include "esp_mac.h"
#include "esp_partition.h"
#include "esp_random.h"
#include "esp_rom_sys.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "nvs.h"
#include "sys_storage.h"
#include "unity.h"

#define PROBE_KEY "t_probe"
#define EMPTY_KEY "t_empty"
#define SEED_KEY_A "t_seed_a"
#define SEED_KEY_B "t_seed_b"
#define CUTS 20
#define CUT_MAGIC 0x43555453u
#define CUT_WINDOW_US 300000
#define CUT_FILE_BYTES 12288
#define TIMER_HZ 1000000
#define SECTOR_BYTES 4096
#define ID_CAP 33
#define MAC_ID_CAP 16

RTC_NOINIT_ATTR static uint32_t s_cut_magic;
RTC_NOINIT_ATTR static uint32_t s_cuts;
RTC_NOINIT_ATTR static uint32_t s_cuts_bad;
RTC_NOINIT_ATTR static uint32_t s_cuts_old;
RTC_NOINIT_ATTR static uint32_t s_cuts_mid;
RTC_NOINIT_ATTR static uint32_t s_confirmed_ver;

static uint8_t s_expect[CUT_FILE_BYTES];
static uint8_t s_read[CUT_FILE_BYTES];
static uint8_t s_sector[SECTOR_BYTES];
static const char *const kNamespaces[] = {STORAGE_NS_WIFI, STORAGE_NS_DEVICE, STORAGE_NS_CALIB,
                                          STORAGE_NS_AFE,  STORAGE_NS_KWS,    STORAGE_NS_MODEL,
                                          STORAGE_NS_SYS};

static void fill_version(uint8_t *buf, uint32_t ver)
{
    uint32_t x = ver * 2654435761u + 1u;
    memcpy(buf, &ver, sizeof(ver));
    for (size_t i = sizeof(ver); i < CUT_FILE_BYTES; i++) {
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        buf[i] = (uint8_t)x;
    }
}

static bool file_holds(uint32_t ver)
{
    size_t len = 0;
    if (sys_storage_read_file(STORAGE_PATH_COMMANDS, s_read, sizeof(s_read), &len) != ESP_OK ||
        len != CUT_FILE_BYTES) {
        return false;
    }
    fill_version(s_expect, ver);
    return memcmp(s_read, s_expect, CUT_FILE_BYTES) == 0;
}

static esp_err_t write_version(uint32_t ver)
{
    fill_version(s_expect, ver);
    return sys_storage_write_file(STORAGE_PATH_COMMANDS, s_expect, CUT_FILE_BYTES);
}

// Resets from an IRAM ISR, so the cut can land while LittleFS waits on a flash erase or program.
static bool IRAM_ATTR cut_power(gptimer_handle_t timer, const gptimer_alarm_event_data_t *edata, void *ctx)
{
    esp_rom_software_reset_system();
    return false;
}

static void arm_cut(uint32_t delay_us)
{
    gptimer_handle_t timer = NULL;
    const gptimer_config_t cfg = {
        .clk_src = GPTIMER_CLK_SRC_DEFAULT, .direction = GPTIMER_COUNT_UP, .resolution_hz = TIMER_HZ};
    ESP_ERROR_CHECK(gptimer_new_timer(&cfg, &timer));
    const gptimer_event_callbacks_t cbs = {.on_alarm = cut_power};
    ESP_ERROR_CHECK(gptimer_register_event_callbacks(timer, &cbs, NULL));
    const gptimer_alarm_config_t alarm = {.alarm_count = delay_us};
    ESP_ERROR_CHECK(gptimer_set_alarm_action(timer, &alarm));
    ESP_ERROR_CHECK(gptimer_enable(timer));
    ESP_ERROR_CHECK(gptimer_start(timer));
}

static void power_cut_cycle(void)
{
    if (esp_reset_reason() == ESP_RST_SW && s_cut_magic == CUT_MAGIC) {
        struct stat st;
        s_cuts_mid += stat(STORAGE_PATH_COMMANDS ".tmp", &st) == 0 ? 1 : 0;
        const bool old = file_holds(s_confirmed_ver);
        s_cuts_old += old ? 1 : 0;
        s_cuts_bad += (old || file_holds(s_confirmed_ver + 1)) ? 0 : 1;
        s_cuts++;
    } else {
        s_cut_magic = CUT_MAGIC;
        s_cuts = s_cuts_bad = s_cuts_old = s_cuts_mid = s_confirmed_ver = 0;
        const int64_t t0 = esp_timer_get_time();
        ESP_ERROR_CHECK(write_version(0));
        printf("MEASURE one %d B set.json replace: %lld us\n", CUT_FILE_BYTES,
               (long long)(esp_timer_get_time() - t0));
    }
    if (s_cuts >= CUTS) {
        s_cut_magic = 0;
        return;
    }
    arm_cut(1 + esp_random() % CUT_WINDOW_US);
    for (uint32_t ver = s_confirmed_ver + 1;; ver++) {
        if (write_version(ver) == ESP_OK) { s_confirmed_ver = ver; }
    }
}

TEST_CASE("power cuts in the middle of set.json writes leave the old or the new file, whole", "[sys_storage]")
{
    printf("MEASURE %lu cuts, %lu mid-write: %lu kept the old file, %lu the new one, %lu broken\n",
           (unsigned long)s_cuts, (unsigned long)s_cuts_mid, (unsigned long)s_cuts_old,
           (unsigned long)(s_cuts - s_cuts_old - s_cuts_bad), (unsigned long)s_cuts_bad);
    TEST_ASSERT_EQUAL_UINT32(CUTS, s_cuts);
    TEST_ASSERT_EQUAL_UINT32(0, s_cuts_bad);
    TEST_ASSERT_GREATER_THAN_UINT32(CUTS / 2, s_cuts_mid);
    TEST_ASSERT_EQUAL(ESP_OK, write_version(s_confirmed_ver + 1));
    TEST_ASSERT_TRUE(file_holds(s_confirmed_ver + 1));
    TEST_ASSERT_EQUAL(0, unlink(STORAGE_PATH_COMMANDS));
}

TEST_CASE("a second init is refused", "[sys_storage]")
{
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_STATE, sys_storage_init());
}

TEST_CASE("every namespace stores a key, reads it back and forgets it once erased", "[sys_storage]")
{
    char out[16];
    for (size_t i = 0; i < sizeof(kNamespaces) / sizeof(kNamespaces[0]); i++) {
        TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_str(kNamespaces[i], PROBE_KEY, kNamespaces[i]));
        TEST_ASSERT_EQUAL(ESP_OK, sys_storage_get_str(kNamespaces[i], PROBE_KEY, out, sizeof(out)));
        TEST_ASSERT_EQUAL_STRING(kNamespaces[i], out);
        TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(kNamespaces[i], PROBE_KEY));
        TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND,
                          sys_storage_get_str(kNamespaces[i], PROBE_KEY, out, sizeof(out)));
        TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(kNamespaces[i], PROBE_KEY));
    }
}

TEST_CASE("an empty string reads as absent and cannot be written", "[sys_storage]")
{
    nvs_handle_t handle;
    TEST_ASSERT_EQUAL(ESP_OK, nvs_open(STORAGE_NS_WIFI, NVS_READWRITE, &handle));
    TEST_ASSERT_EQUAL(ESP_OK, nvs_set_str(handle, EMPTY_KEY, ""));
    TEST_ASSERT_EQUAL(ESP_OK, nvs_commit(handle));
    nvs_close(handle);

    char out[8] = "x";
    TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND, sys_storage_get_str(STORAGE_NS_WIFI, EMPTY_KEY, out, sizeof(out)));
    TEST_ASSERT_EQUAL_STRING("", out);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, sys_storage_set_str(STORAGE_NS_WIFI, EMPTY_KEY, ""));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_WIFI, EMPTY_KEY));
}

TEST_CASE("a string longer than the buffer is refused, not cut", "[sys_storage]")
{
    char out[4] = "x";
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_str(STORAGE_NS_DEVICE, PROBE_KEY, "abcdefgh"));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_SIZE,
                      sys_storage_get_str(STORAGE_NS_DEVICE, PROBE_KEY, out, sizeof(out)));
    TEST_ASSERT_EQUAL_STRING("", out);
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_DEVICE, PROBE_KEY));
}

TEST_CASE("each integer type round-trips and reads back only through its own type", "[sys_storage]")
{
    uint8_t u8 = 0;
    int8_t i8 = 0;
    uint16_t u16 = 0;
    uint32_t u32 = 0;
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_u8(STORAGE_NS_CALIB, PROBE_KEY, 200));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_get_u8(STORAGE_NS_CALIB, PROBE_KEY, &u8));
    TEST_ASSERT_EQUAL_UINT8(200, u8);
    TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND, sys_storage_get_u16(STORAGE_NS_CALIB, PROBE_KEY, &u16));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_CALIB, PROBE_KEY));

    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_i8(STORAGE_NS_AFE, PROBE_KEY, -40));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_get_i8(STORAGE_NS_AFE, PROBE_KEY, &i8));
    TEST_ASSERT_EQUAL_INT8(-40, i8);
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_AFE, PROBE_KEY));

    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_u16(STORAGE_NS_KWS, PROBE_KEY, 65000));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_get_u16(STORAGE_NS_KWS, PROBE_KEY, &u16));
    TEST_ASSERT_EQUAL_UINT16(65000, u16);
    TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND, sys_storage_get_u8(STORAGE_NS_KWS, PROBE_KEY, &u8));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_KWS, PROBE_KEY));

    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_u32(STORAGE_NS_SYS, PROBE_KEY, UINT32_MAX));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_get_u32(STORAGE_NS_SYS, PROBE_KEY, &u32));
    TEST_ASSERT_EQUAL_UINT32(UINT32_MAX, u32);
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_SYS, PROBE_KEY));
    TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND, sys_storage_get_u32(STORAGE_NS_SYS, PROBE_KEY, &u32));
}

TEST_CASE("a blob reads back only at its exact length", "[sys_storage]")
{
    static float bal[STORAGE_CALIB_BAL_BYTES / sizeof(float)];
    static float back[STORAGE_CALIB_BAL_BYTES / sizeof(float)];
    for (size_t i = 0; i < sizeof(bal) / sizeof(bal[0]); i++) {
        bal[i] = (float)i * 0.5f - 3.0f;
    }
    TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND,
                      sys_storage_get_blob(STORAGE_NS_CALIB, PROBE_KEY, back, sizeof(back)));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_blob(STORAGE_NS_CALIB, PROBE_KEY, bal, sizeof(bal)));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_get_blob(STORAGE_NS_CALIB, PROBE_KEY, back, sizeof(back)));
    TEST_ASSERT_EQUAL_MEMORY(bal, back, sizeof(bal));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_SIZE, sys_storage_get_blob(STORAGE_NS_CALIB, PROBE_KEY, back, 100));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_CALIB, PROBE_KEY));
}

TEST_CASE("seeds fill absent keys, keep changed ones, and a newer seed set overwrites them", "[sys_storage]")
{
    uint32_t saved_ver = 0;
    const bool had_ver = sys_storage_get_u32(STORAGE_NS_SYS, STORAGE_KEY_SEED_VER, &saved_ver) == ESP_OK;
    const uint32_t base = had_ver ? saved_ver : 0;
    const sys_storage_seed_t seeds[] = {
        {STORAGE_NS_AFE, SEED_KEY_A, SYS_STORAGE_I8, -30},
        {STORAGE_NS_KWS, SEED_KEY_B, SYS_STORAGE_U16, 850},
    };
    int8_t a = 0;
    uint16_t b = 0;
    uint32_t ver = 0;

    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_seed(base, seeds, 2));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_get_i8(STORAGE_NS_AFE, SEED_KEY_A, &a));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_get_u16(STORAGE_NS_KWS, SEED_KEY_B, &b));
    TEST_ASSERT_EQUAL_INT8(-30, a);
    TEST_ASSERT_EQUAL_UINT16(850, b);

    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_i8(STORAGE_NS_AFE, SEED_KEY_A, -25));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_seed(base, seeds, 2));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_get_i8(STORAGE_NS_AFE, SEED_KEY_A, &a));
    TEST_ASSERT_EQUAL_INT8(-25, a);

    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_seed(base + 1, seeds, 2));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_get_i8(STORAGE_NS_AFE, SEED_KEY_A, &a));
    TEST_ASSERT_EQUAL_INT8(-30, a);
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_get_u32(STORAGE_NS_SYS, STORAGE_KEY_SEED_VER, &ver));
    TEST_ASSERT_EQUAL_UINT32(base + 1, ver);

    const sys_storage_seed_t too_big[] = {{STORAGE_NS_AFE, SEED_KEY_A, SYS_STORAGE_U8, 300}};
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, sys_storage_seed(base + 2, too_big, 1));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_get_u32(STORAGE_NS_SYS, STORAGE_KEY_SEED_VER, &ver));
    TEST_ASSERT_EQUAL_UINT32(base + 1, ver);

    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_AFE, SEED_KEY_A));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_KWS, SEED_KEY_B));
    TEST_ASSERT_EQUAL(ESP_OK, had_ver ? sys_storage_set_u32(STORAGE_NS_SYS, STORAGE_KEY_SEED_VER, saved_ver)
                                      : sys_storage_erase(STORAGE_NS_SYS, STORAGE_KEY_SEED_VER));
}

TEST_CASE("the boot count matches NVS and counts the cut reboots", "[sys_storage]")
{
    uint32_t stored = 0;
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_get_u32(STORAGE_NS_SYS, STORAGE_KEY_BOOT_COUNT, &stored));
    TEST_ASSERT_EQUAL_UINT32(stored, sys_storage_boot_count());
    TEST_ASSERT_GREATER_THAN_UINT32(CUTS, sys_storage_boot_count());
}

TEST_CASE("the device id is the serial when set, else sr- and the factory MAC", "[sys_storage]")
{
    char saved[ID_CAP];
    const bool had_serial =
        sys_storage_get_str(STORAGE_NS_DEVICE, STORAGE_KEY_SERIAL, saved, sizeof(saved)) == ESP_OK;
    char id[ID_CAP];
    char want[ID_CAP];
    uint8_t mac[6];

    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_erase(STORAGE_NS_DEVICE, STORAGE_KEY_SERIAL));
    TEST_ASSERT_EQUAL(ESP_OK, esp_efuse_mac_get_default(mac));
    snprintf(want, sizeof(want), "sr-%02x%02x%02x%02x%02x%02x", mac[0], mac[1], mac[2], mac[3], mac[4],
             mac[5]);
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_device_id(id, MAC_ID_CAP));
    TEST_ASSERT_EQUAL_STRING(want, id);
    printf("MEASURE deviceId %s\n", id);
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_SIZE, sys_storage_device_id(id, MAC_ID_CAP - 1));

    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_set_str(STORAGE_NS_DEVICE, STORAGE_KEY_SERIAL, "bench-01"));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_device_id(id, sizeof(id)));
    TEST_ASSERT_EQUAL_STRING("bench-01", id);

    TEST_ASSERT_EQUAL(ESP_OK, had_serial ? sys_storage_set_str(STORAGE_NS_DEVICE, STORAGE_KEY_SERIAL, saved)
                                         : sys_storage_erase(STORAGE_NS_DEVICE, STORAGE_KEY_SERIAL));
}

TEST_CASE("a replaced file reads back whole, makes its directories and leaves no temporary", "[sys_storage]")
{
    static const char path[] = STORAGE_LFS_MOUNT "/t_a/t_b/probe.bin";
    static const char payload[] = "sys_storage probe";
    char back[sizeof(payload)];
    size_t len = 0;
    struct stat st;

    TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND, sys_storage_read_file(path, back, sizeof(back), &len));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_write_file(path, payload, sizeof(payload)));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_read_file(path, back, sizeof(back), &len));
    TEST_ASSERT_EQUAL(sizeof(payload), len);
    TEST_ASSERT_EQUAL_STRING(payload, back);
    TEST_ASSERT_NOT_EQUAL(0, stat(STORAGE_LFS_MOUNT "/t_a/t_b/probe.bin.tmp", &st));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_SIZE, sys_storage_read_file(path, back, sizeof(back) - 1, &len));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, sys_storage_write_file("/spiffs/x.bin", payload, sizeof(payload)));

    TEST_ASSERT_EQUAL(0, unlink(path));
    TEST_ASSERT_EQUAL(0, rmdir(STORAGE_LFS_MOUNT "/t_a/t_b"));
    TEST_ASSERT_EQUAL(0, rmdir(STORAGE_LFS_MOUNT "/t_a"));
}

TEST_CASE("a models slot maps only with a sound header", "[sys_storage]")
{
    const esp_partition_t *part = esp_partition_find_first(ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY,
                                                           STORAGE_MODEL_LABEL_SLOT1);
    TEST_ASSERT_NOT_NULL(part);
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_read(part, 0, s_sector, sizeof(s_sector)));
    sys_storage_models_t models = {0};
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_ARG, sys_storage_map_models(2, &models));

    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, SECTOR_BYTES));
    TEST_ASSERT_EQUAL(ESP_ERR_NOT_FOUND, sys_storage_map_models(1, &models));

    static storage_model_header_t header;
    memset(&header, 0, sizeof(header));
    header.magic = STORAGE_MODEL_MAGIC;
    header.format_ver = STORAGE_MODEL_FORMAT_VER;
    header.count = 1;
    header.grid_hash = GEN_GRID_HASH;
    strcpy(header.entry[0].name, "wake");
    header.entry[0].offset = STORAGE_MODEL_HEADER_BYTES;
    header.entry[0].size = 1000;
    header.entry[0].kind = STORAGE_MODEL_KIND_ESPDL;
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_write(part, 0, &header, sizeof(header)));
    TEST_ASSERT_EQUAL(ESP_OK, sys_storage_map_models(1, &models));
    TEST_ASSERT_EQUAL_UINT32(GEN_GRID_HASH, models.header->grid_hash);
    TEST_ASSERT_EQUAL_STRING("wake", models.header->entry[0].name);
    TEST_ASSERT_EQUAL(part->size, models.partition_bytes);
    sys_storage_unmap_models(&models);
    TEST_ASSERT_NULL(models.header);

    const struct {
        uint32_t format_ver;
        uint32_t offset;
        esp_err_t want;
    } bad[] = {
        {STORAGE_MODEL_FORMAT_VER + 1, STORAGE_MODEL_HEADER_BYTES, ESP_ERR_INVALID_VERSION},
        {STORAGE_MODEL_FORMAT_VER, STORAGE_MODEL_HEADER_BYTES + 8, ESP_ERR_INVALID_SIZE},
        {STORAGE_MODEL_FORMAT_VER, 0, ESP_ERR_INVALID_SIZE},
    };
    for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        header.format_ver = bad[i].format_ver;
        header.entry[0].offset = bad[i].offset;
        TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, SECTOR_BYTES));
        TEST_ASSERT_EQUAL(ESP_OK, esp_partition_write(part, 0, &header, sizeof(header)));
        TEST_ASSERT_EQUAL(bad[i].want, sys_storage_map_models(1, &models));
    }

    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_erase_range(part, 0, SECTOR_BYTES));
    TEST_ASSERT_EQUAL(ESP_OK, esp_partition_write(part, 0, s_sector, sizeof(s_sector)));
}

void app_main(void)
{
    ESP_ERROR_CHECK(sys_storage_init());
    power_cut_cycle();
    UNITY_BEGIN();
    unity_run_all_tests();
    UNITY_END();
}
