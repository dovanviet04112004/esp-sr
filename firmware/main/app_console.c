#include "app_console.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "argtable3/argtable3.h"
#include "esp_check.h"
#include "esp_console.h"
#include "esp_log.h"
#include "esp_wifi_types.h"
#include "sys_storage.h"

#define REPL_STACK_BYTES 4096
#define REPL_PRIORITY 2
#define REPL_CORE 0
#define CMDLINE_BYTES 256
#define VALUE_BYTES 192
#define PROMPT "sr> "
#define SSID_MAX_BYTES sizeof(((wifi_sta_config_t *)NULL)->ssid)
#define PASS_MAX_BYTES (sizeof(((wifi_sta_config_t *)NULL)->password) - 1)
#define PASS_MIN_BYTES 8 // WPA2 passphrase, IEEE 802.11i

typedef enum {
    TYPE_STR = 0,
    TYPE_U8,
    TYPE_I8,
    TYPE_U16,
    TYPE_U32,
    TYPE_COUNT,
} value_type_t;

static const char *TAG = "app_console";
static const char *const kTypeNames[TYPE_COUNT] = {"str", "u8", "i8", "u16", "u32"};
static const int64_t kTypeMin[TYPE_COUNT] = {0, 0, INT8_MIN, 0, 0};
static const int64_t kTypeMax[TYPE_COUNT] = {0, UINT8_MAX, INT8_MAX, UINT16_MAX, UINT32_MAX};

static struct {
    struct arg_str *action;
    struct arg_str *ssid;
    struct arg_str *pass;
    struct arg_end *end;
} s_wifi_args;

static struct {
    struct arg_str *action;
    struct arg_str *ns;
    struct arg_str *key;
    struct arg_str *value;
    struct arg_str *type;
    struct arg_end *end;
} s_nvs_args;

static bool is_secret(const char *key)
{
    return strcmp(key, STORAGE_KEY_PASS) == 0 || strcmp(key, STORAGE_KEY_MQTT_PASS) == 0;
}

static int wifi_cmd(int argc, char **argv)
{
    if (arg_parse(argc, argv, (void **)&s_wifi_args) != 0 ||
        strcmp(s_wifi_args.action->sval[0], "set") != 0) {
        printf("usage: wifi set <ssid> [pass]\n");
        return 1;
    }
    const char *ssid = s_wifi_args.ssid->sval[0];
    const char *pass = s_wifi_args.pass->count > 0 ? s_wifi_args.pass->sval[0] : "";
    const size_t pass_len = strlen(pass);
    if (strlen(ssid) > SSID_MAX_BYTES || pass_len > PASS_MAX_BYTES ||
        (pass_len > 0 && pass_len < PASS_MIN_BYTES)) {
        printf("ssid is 1..%u bytes, pass is empty or %u..%u bytes\n", (unsigned)SSID_MAX_BYTES,
               (unsigned)PASS_MIN_BYTES, (unsigned)PASS_MAX_BYTES);
        return 1;
    }
    esp_err_t err = sys_storage_set_str(STORAGE_NS_WIFI, STORAGE_KEY_SSID, ssid);
    if (err == ESP_OK) {
        err = pass_len > 0 ? sys_storage_set_str(STORAGE_NS_WIFI, STORAGE_KEY_PASS, pass)
                           : sys_storage_erase(STORAGE_NS_WIFI, STORAGE_KEY_PASS);
    }
    printf("wifi/ssid %s, wifi/pass %u chars: %s\n", ssid, (unsigned)pass_len, esp_err_to_name(err));
    return err == ESP_OK ? 0 : 1;
}

static esp_err_t read_int(const char *ns, const char *key, value_type_t type, int64_t *out)
{
    uint8_t u8 = 0;
    int8_t i8 = 0;
    uint16_t u16 = 0;
    uint32_t u32 = 0;
    esp_err_t err = ESP_ERR_INVALID_ARG;
    switch (type) {
    case TYPE_U8:
        err = sys_storage_get_u8(ns, key, &u8);
        *out = u8;
        break;
    case TYPE_I8:
        err = sys_storage_get_i8(ns, key, &i8);
        *out = i8;
        break;
    case TYPE_U16:
        err = sys_storage_get_u16(ns, key, &u16);
        *out = u16;
        break;
    case TYPE_U32:
        err = sys_storage_get_u32(ns, key, &u32);
        *out = u32;
        break;
    default: break;
    }
    return err;
}

static esp_err_t write_int(const char *ns, const char *key, value_type_t type, int64_t value)
{
    switch (type) {
    case TYPE_U8: return sys_storage_set_u8(ns, key, (uint8_t)value);
    case TYPE_I8: return sys_storage_set_i8(ns, key, (int8_t)value);
    case TYPE_U16: return sys_storage_set_u16(ns, key, (uint16_t)value);
    case TYPE_U32: return sys_storage_set_u32(ns, key, (uint32_t)value);
    default: return ESP_ERR_INVALID_ARG;
    }
}

// NVS lookups are typed, so a key is found by asking for each type of KEHOACH 6.2 in turn.
static int nvs_get(const char *ns, const char *key)
{
    char text[VALUE_BYTES];
    const esp_err_t err = sys_storage_get_str(ns, key, text, sizeof(text));
    if (err == ESP_OK) {
        if (is_secret(key)) {
            printf("%s/%s: str, %u chars\n", ns, key, (unsigned)strlen(text));
        } else {
            printf("%s/%s = \"%s\" (str)\n", ns, key, text);
        }
        return 0;
    }
    if (err == ESP_ERR_INVALID_SIZE) {
        printf("%s/%s: str longer than %u bytes\n", ns, key, (unsigned)sizeof(text) - 1);
        return 1;
    }
    for (value_type_t type = TYPE_U8; type < TYPE_COUNT; type++) {
        int64_t value = 0;
        if (read_int(ns, key, type, &value) == ESP_OK) {
            printf("%s/%s = %" PRId64 " (%s)\n", ns, key, value, kTypeNames[type]);
            return 0;
        }
    }
    printf("%s/%s: absent\n", ns, key);
    return 1;
}

static int nvs_set(const char *ns, const char *key, const char *value, const char *type_name)
{
    value_type_t type = TYPE_COUNT;
    for (value_type_t t = TYPE_STR; t < TYPE_COUNT; t++) {
        if (strcmp(type_name, kTypeNames[t]) == 0) { type = t; }
    }
    if (type == TYPE_COUNT) {
        printf("type is one of str u8 i8 u16 u32\n");
        return 1;
    }
    esp_err_t err = ESP_OK;
    if (type == TYPE_STR) {
        err = sys_storage_set_str(ns, key, value);
    } else {
        char *end = NULL;
        const long long parsed = strtoll(value, &end, 0);
        if (*value == '\0' || *end != '\0' || parsed < kTypeMin[type] || parsed > kTypeMax[type]) {
            printf("%s is not a %s\n", value, kTypeNames[type]);
            return 1;
        }
        err = write_int(ns, key, type, parsed);
    }
    if (is_secret(key)) {
        printf("%s/%s set to %u chars: %s\n", ns, key, (unsigned)strlen(value), esp_err_to_name(err));
    } else {
        printf("%s/%s = %s (%s): %s\n", ns, key, value, kTypeNames[type], esp_err_to_name(err));
    }
    return err == ESP_OK ? 0 : 1;
}

static int nvs_cmd(int argc, char **argv)
{
    if (arg_parse(argc, argv, (void **)&s_nvs_args) != 0) {
        arg_print_errors(stdout, s_nvs_args.end, argv[0]);
        return 1;
    }
    const char *action = s_nvs_args.action->sval[0];
    const char *ns = s_nvs_args.ns->sval[0];
    const char *key = s_nvs_args.key->sval[0];
    if (strcmp(action, "get") == 0) { return nvs_get(ns, key); }
    if (strcmp(action, "set") == 0 && s_nvs_args.value->count > 0) {
        const char *type = s_nvs_args.type->count > 0 ? s_nvs_args.type->sval[0] : kTypeNames[TYPE_STR];
        return nvs_set(ns, key, s_nvs_args.value->sval[0], type);
    }
    if (strcmp(action, "del") == 0) {
        const esp_err_t err = sys_storage_erase(ns, key);
        printf("%s/%s dropped: %s\n", ns, key, esp_err_to_name(err));
        return err == ESP_OK ? 0 : 1;
    }
    printf("usage: nvs get <ns> <key> | nvs set <ns> <key> <value> [-t type] | nvs del <ns> <key>\n");
    return 1;
}

static esp_err_t register_commands(void)
{
    s_wifi_args.action = arg_str1(NULL, NULL, "set", "the only action");
    s_wifi_args.ssid = arg_str1(NULL, NULL, "<ssid>", "network name");
    s_wifi_args.pass = arg_str0(NULL, NULL, "<pass>", "password; leave out for an open network");
    s_wifi_args.end = arg_end(3);
    const esp_console_cmd_t wifi = {
        .command = "wifi",
        .help = "Store the network in NVS wifi/ssid and wifi/pass",
        .func = wifi_cmd,
        .argtable = &s_wifi_args,
    };
    ESP_RETURN_ON_ERROR(esp_console_cmd_register(&wifi), TAG, "wifi");

    s_nvs_args.action = arg_str1(NULL, NULL, "get|set|del", "what to do");
    s_nvs_args.ns = arg_str1(NULL, NULL, "<ns>", "namespace of KEHOACH 6.2");
    s_nvs_args.key = arg_str1(NULL, NULL, "<key>", "key inside it");
    s_nvs_args.value = arg_str0(NULL, NULL, "<value>", "value to store, for set");
    s_nvs_args.type = arg_str0("t", "type", "<str|u8|i8|u16|u32>", "type to store, str when left out");
    s_nvs_args.end = arg_end(5);
    const esp_console_cmd_t nvs = {
        .command = "nvs",
        .help = "Read, write or drop one NVS key; get finds the stored type by itself",
        .func = nvs_cmd,
        .argtable = &s_nvs_args,
    };
    return esp_console_cmd_register(&nvs);
}

esp_err_t app_console_start(void)
{
    esp_console_repl_config_t config = ESP_CONSOLE_REPL_CONFIG_DEFAULT();
    config.prompt = PROMPT;
    config.max_cmdline_length = CMDLINE_BYTES;
    config.task_stack_size = REPL_STACK_BYTES;
    config.task_priority = REPL_PRIORITY;
    config.task_core_id = REPL_CORE;
    // History stays in RAM: saved to flash, a typed password would outlive the session.
    config.history_save_path = NULL;
    const esp_console_dev_uart_config_t uart = ESP_CONSOLE_DEV_UART_CONFIG_DEFAULT();
    esp_console_repl_t *repl = NULL;
    ESP_RETURN_ON_ERROR(esp_console_new_repl_uart(&uart, &config, &repl), TAG, "repl");
    ESP_RETURN_ON_ERROR(esp_console_register_help_command(), TAG, "help");
    ESP_RETURN_ON_ERROR(register_commands(), TAG, "commands");
    ESP_RETURN_ON_ERROR(esp_console_start_repl(repl), TAG, "start");
    ESP_LOGW(TAG, "console on UART0 writes every NVS key: a dev build, never ship it");
    return ESP_OK;
}
