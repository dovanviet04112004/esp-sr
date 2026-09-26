// GENERATED FILE - DO NOT EDIT.
// Source: contracts/schema/*.schema.json
// Regenerate: python3 tools/gen_contracts.py

#pragma once

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "cJSON.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    char id[33];
    char text[257];
    char response[33];
    bool has_response;
} command_set_commands_item_t;

/** Fill out from a parsed command_set_commands object, checking type, range, enum and size.
 *  @ctx any | non-blocking | out is cleared first; strings are copied, not borrowed
 *  @ret false on the first field outside the contract; out is then partly filled
 */
static inline bool command_set_commands_item_from_json(const cJSON *root, command_set_commands_item_t *out)
{
    if (!cJSON_IsObject(root) || out == NULL) { return false; }
    memset(out, 0, sizeof(*out));
    const cJSON *item = NULL;
    item = cJSON_GetObjectItemCaseSensitive(root, "id");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->id)) { return false; }
        strcpy(out->id, item->valuestring);
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "text");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->text)) { return false; }
        strcpy(out->text, item->valuestring);
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "response");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->response)) { return false; }
        strcpy(out->response, item->valuestring);
        out->has_response = true;
    }
    return true;
}

/** Build the JSON object of one command_set_commands for publishing.
 *  @ctx any | non-blocking | allocates through cJSON: caller frees with cJSON_Delete
 *  @ret NULL when the root object cannot be allocated
 */
static inline cJSON *command_set_commands_item_to_json(const command_set_commands_item_t *in)
{
    if (in == NULL) { return NULL; }
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) { return NULL; }
    cJSON_AddStringToObject(root, "id", in->id);
    cJSON_AddStringToObject(root, "text", in->text);
    if (in->has_response) {
        cJSON_AddStringToObject(root, "response", in->response);
    }
    return root;
}

typedef struct {
    uint32_t version;
    command_set_commands_item_t commands[64];
    uint8_t commands_count;
} command_set_t;

/** Fill out from a parsed command_set object, checking type, range, enum and size.
 *  @ctx any | non-blocking | out is cleared first; strings are copied, not borrowed
 *  @ret false on the first field outside the contract; out is then partly filled
 */
static inline bool command_set_from_json(const cJSON *root, command_set_t *out)
{
    if (!cJSON_IsObject(root) || out == NULL) { return false; }
    memset(out, 0, sizeof(*out));
    const cJSON *item = NULL;
    item = cJSON_GetObjectItemCaseSensitive(root, "version");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 1.0 || item->valuedouble > 2147483647.0) { return false; }
        out->version = (uint32_t) item->valuedouble;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "commands");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsArray(item) || cJSON_GetArraySize(item) > 64) { return false; }
        if (cJSON_GetArraySize(item) < 1) { return false; }
        const cJSON *el = NULL;
        cJSON_ArrayForEach(el, item) {
            if (!command_set_commands_item_from_json(el, &out->commands[out->commands_count])) { return false; }
            out->commands_count++;
        }
    }
    return true;
}

/** Build the JSON object of one command_set for publishing.
 *  @ctx any | non-blocking | allocates through cJSON: caller frees with cJSON_Delete
 *  @ret NULL when the root object cannot be allocated
 */
static inline cJSON *command_set_to_json(const command_set_t *in)
{
    if (in == NULL) { return NULL; }
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) { return NULL; }
    cJSON_AddNumberToObject(root, "version", (double) in->version);
    cJSON *commands_arr = cJSON_AddArrayToObject(root, "commands");
    for (int i = 0; commands_arr != NULL && i < in->commands_count; i++) {
        cJSON_AddItemToArray(commands_arr, command_set_commands_item_to_json(&in->commands[i]));
    }
    return root;
}

typedef enum {
    DEVICE_CMD_OP_SET_CONFIG = 0,
    DEVICE_CMD_OP_SET_STREAM = 1,
    DEVICE_CMD_OP_SPEAK = 2,
    DEVICE_CMD_OP_CALIBRATE = 3,
    DEVICE_CMD_OP_REBOOT = 4,
    DEVICE_CMD_OP_COUNT = 5,
} device_cmd_op_t;

/** Contract spelling of a value; "" when it is out of range.
 *  @ctx any | non-blocking | returns a static string
 */
static inline const char *device_cmd_op_str(device_cmd_op_t v)
{
    switch (v) {
    case DEVICE_CMD_OP_SET_CONFIG: return "SET_CONFIG";
    case DEVICE_CMD_OP_SET_STREAM: return "SET_STREAM";
    case DEVICE_CMD_OP_SPEAK: return "SPEAK";
    case DEVICE_CMD_OP_CALIBRATE: return "CALIBRATE";
    case DEVICE_CMD_OP_REBOOT: return "REBOOT";
    default: return "";
    }
}

/** Value of a contract spelling.
 *  @ctx any | non-blocking
 *  @ret false for NULL or a spelling the contract does not list
 */
static inline bool device_cmd_op_parse(const char *s, device_cmd_op_t *out)
{
    if (s == NULL || out == NULL) { return false; }
    if (strcmp(s, "SET_CONFIG") == 0) { *out = DEVICE_CMD_OP_SET_CONFIG; return true; }
    if (strcmp(s, "SET_STREAM") == 0) { *out = DEVICE_CMD_OP_SET_STREAM; return true; }
    if (strcmp(s, "SPEAK") == 0) { *out = DEVICE_CMD_OP_SPEAK; return true; }
    if (strcmp(s, "CALIBRATE") == 0) { *out = DEVICE_CMD_OP_CALIBRATE; return true; }
    if (strcmp(s, "REBOOT") == 0) { *out = DEVICE_CMD_OP_REBOOT; return true; }
    return false;
}

typedef struct {
    uint8_t mode;
    char host[65];
    uint16_t port;
    uint16_t duration_s;
    bool has_host;
    bool has_port;
    bool has_duration_s;
} device_cmd_stream_t;

/** Fill out from a parsed device_cmd_stream object, checking type, range, enum and size.
 *  @ctx any | non-blocking | out is cleared first; strings are copied, not borrowed
 *  @ret false on the first field outside the contract; out is then partly filled
 */
static inline bool device_cmd_stream_from_json(const cJSON *root, device_cmd_stream_t *out)
{
    if (!cJSON_IsObject(root) || out == NULL) { return false; }
    memset(out, 0, sizeof(*out));
    const cJSON *item = NULL;
    item = cJSON_GetObjectItemCaseSensitive(root, "mode");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 4.0) { return false; }
        out->mode = (uint8_t) item->valuedouble;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "host");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->host)) { return false; }
        strcpy(out->host, item->valuestring);
        out->has_host = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "port");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 1.0 || item->valuedouble > 65535.0) { return false; }
        out->port = (uint16_t) item->valuedouble;
        out->has_port = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "durationS");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 1.0 || item->valuedouble > 3600.0) { return false; }
        out->duration_s = (uint16_t) item->valuedouble;
        out->has_duration_s = true;
    }
    return true;
}

/** Build the JSON object of one device_cmd_stream for publishing.
 *  @ctx any | non-blocking | allocates through cJSON: caller frees with cJSON_Delete
 *  @ret NULL when the root object cannot be allocated
 */
static inline cJSON *device_cmd_stream_to_json(const device_cmd_stream_t *in)
{
    if (in == NULL) { return NULL; }
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) { return NULL; }
    cJSON_AddNumberToObject(root, "mode", (double) in->mode);
    if (in->has_host) {
        cJSON_AddStringToObject(root, "host", in->host);
    }
    if (in->has_port) {
        cJSON_AddNumberToObject(root, "port", (double) in->port);
    }
    if (in->has_duration_s) {
        cJSON_AddNumberToObject(root, "durationS", (double) in->duration_s);
    }
    return root;
}

typedef struct {
    device_cmd_op_t op;
    char request_id[17];
    char key[32];
    int32_t value;
    device_cmd_stream_t stream;
    char text[513];
    bool has_request_id;
    bool has_key;
    bool has_value;
    bool has_stream;
    bool has_text;
} device_cmd_t;

/** Fill out from a parsed device_cmd object, checking type, range, enum and size.
 *  @ctx any | non-blocking | out is cleared first; strings are copied, not borrowed
 *  @ret false on the first field outside the contract; out is then partly filled
 */
static inline bool device_cmd_from_json(const cJSON *root, device_cmd_t *out)
{
    if (!cJSON_IsObject(root) || out == NULL) { return false; }
    memset(out, 0, sizeof(*out));
    const cJSON *item = NULL;
    item = cJSON_GetObjectItemCaseSensitive(root, "op");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || !device_cmd_op_parse(item->valuestring, &out->op)) { return false; }
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "requestId");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->request_id)) { return false; }
        strcpy(out->request_id, item->valuestring);
        out->has_request_id = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "key");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->key)) { return false; }
        strcpy(out->key, item->valuestring);
        out->has_key = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "value");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < -2147483648.0 || item->valuedouble > 2147483647.0) { return false; }
        out->value = (int32_t) item->valuedouble;
        out->has_value = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "stream");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!device_cmd_stream_from_json(item, &out->stream)) { return false; }
        out->has_stream = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "text");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->text)) { return false; }
        strcpy(out->text, item->valuestring);
        out->has_text = true;
    }
    return true;
}

/** Build the JSON object of one device_cmd for publishing.
 *  @ctx any | non-blocking | allocates through cJSON: caller frees with cJSON_Delete
 *  @ret NULL when the root object cannot be allocated
 */
static inline cJSON *device_cmd_to_json(const device_cmd_t *in)
{
    if (in == NULL) { return NULL; }
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) { return NULL; }
    cJSON_AddStringToObject(root, "op", device_cmd_op_str(in->op));
    if (in->has_request_id) {
        cJSON_AddStringToObject(root, "requestId", in->request_id);
    }
    if (in->has_key) {
        cJSON_AddStringToObject(root, "key", in->key);
    }
    if (in->has_value) {
        cJSON_AddNumberToObject(root, "value", (double) in->value);
    }
    if (in->has_stream) {
        cJSON_AddItemToObject(root, "stream", device_cmd_stream_to_json(&in->stream));
    }
    if (in->has_text) {
        cJSON_AddStringToObject(root, "text", in->text);
    }
    return root;
}

typedef enum {
    EVENT_KIND_WAKE = 0,
    EVENT_KIND_COMMAND = 1,
    EVENT_KIND_REJECT = 2,
    EVENT_KIND_ERROR = 3,
    EVENT_KIND_COUNT = 4,
} event_kind_t;

/** Contract spelling of a value; "" when it is out of range.
 *  @ctx any | non-blocking | returns a static string
 */
static inline const char *event_kind_str(event_kind_t v)
{
    switch (v) {
    case EVENT_KIND_WAKE: return "WAKE";
    case EVENT_KIND_COMMAND: return "COMMAND";
    case EVENT_KIND_REJECT: return "REJECT";
    case EVENT_KIND_ERROR: return "ERROR";
    default: return "";
    }
}

/** Value of a contract spelling.
 *  @ctx any | non-blocking
 *  @ret false for NULL or a spelling the contract does not list
 */
static inline bool event_kind_parse(const char *s, event_kind_t *out)
{
    if (s == NULL || out == NULL) { return false; }
    if (strcmp(s, "WAKE") == 0) { *out = EVENT_KIND_WAKE; return true; }
    if (strcmp(s, "COMMAND") == 0) { *out = EVENT_KIND_COMMAND; return true; }
    if (strcmp(s, "REJECT") == 0) { *out = EVENT_KIND_REJECT; return true; }
    if (strcmp(s, "ERROR") == 0) { *out = EVENT_KIND_ERROR; return true; }
    return false;
}

typedef struct {
    char device_id[33];
    uint32_t seq;
    event_kind_t kind;
    uint16_t score_permille;
    uint16_t margin_permille;
    char command_id[33];
    char code[33];
    int16_t doa_deg;
    uint32_t ts;
    bool has_score_permille;
    bool has_margin_permille;
    bool has_command_id;
    bool has_code;
    bool has_doa_deg;
    bool has_ts;
} event_t;

/** Fill out from a parsed event object, checking type, range, enum and size.
 *  @ctx any | non-blocking | out is cleared first; strings are copied, not borrowed
 *  @ret false on the first field outside the contract; out is then partly filled
 */
static inline bool event_from_json(const cJSON *root, event_t *out)
{
    if (!cJSON_IsObject(root) || out == NULL) { return false; }
    memset(out, 0, sizeof(*out));
    const cJSON *item = NULL;
    item = cJSON_GetObjectItemCaseSensitive(root, "deviceId");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->device_id)) { return false; }
        strcpy(out->device_id, item->valuestring);
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "seq");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 4294967295.0) { return false; }
        out->seq = (uint32_t) item->valuedouble;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "kind");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || !event_kind_parse(item->valuestring, &out->kind)) { return false; }
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "scorePermille");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 1000.0) { return false; }
        out->score_permille = (uint16_t) item->valuedouble;
        out->has_score_permille = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "marginPermille");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 1000.0) { return false; }
        out->margin_permille = (uint16_t) item->valuedouble;
        out->has_margin_permille = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "commandId");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->command_id)) { return false; }
        strcpy(out->command_id, item->valuestring);
        out->has_command_id = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "code");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->code)) { return false; }
        strcpy(out->code, item->valuestring);
        out->has_code = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "doaDeg");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < -1.0 || item->valuedouble > 180.0) { return false; }
        out->doa_deg = (int16_t) item->valuedouble;
        out->has_doa_deg = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "ts");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 4294967295.0) { return false; }
        out->ts = (uint32_t) item->valuedouble;
        out->has_ts = true;
    }
    return true;
}

/** Build the JSON object of one event for publishing.
 *  @ctx any | non-blocking | allocates through cJSON: caller frees with cJSON_Delete
 *  @ret NULL when the root object cannot be allocated
 */
static inline cJSON *event_to_json(const event_t *in)
{
    if (in == NULL) { return NULL; }
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) { return NULL; }
    cJSON_AddStringToObject(root, "deviceId", in->device_id);
    cJSON_AddNumberToObject(root, "seq", (double) in->seq);
    cJSON_AddStringToObject(root, "kind", event_kind_str(in->kind));
    if (in->has_score_permille) {
        cJSON_AddNumberToObject(root, "scorePermille", (double) in->score_permille);
    }
    if (in->has_margin_permille) {
        cJSON_AddNumberToObject(root, "marginPermille", (double) in->margin_permille);
    }
    if (in->has_command_id) {
        cJSON_AddStringToObject(root, "commandId", in->command_id);
    }
    if (in->has_code) {
        cJSON_AddStringToObject(root, "code", in->code);
    }
    if (in->has_doa_deg) {
        cJSON_AddNumberToObject(root, "doaDeg", (double) in->doa_deg);
    }
    if (in->has_ts) {
        cJSON_AddNumberToObject(root, "ts", (double) in->ts);
    }
    return root;
}

typedef struct {
    char device_id[33];
    char fw[33];
    char models[33];
    uint32_t uptime_s;
    uint32_t heap_internal_free;
    uint32_t heap_internal_min;
    uint32_t heap_psram_free;
    uint32_t heap_psram_min;
    uint32_t dma_overflows;
    uint32_t frames_dropped;
    uint32_t clean_dropped;
    uint32_t events_dropped;
    uint32_t stream_dropped;
    uint16_t q_clean_peak;
    uint16_t json_arena_peak;
    int8_t rssi_dbm;
    uint8_t core_load_pct[2];
    uint8_t core_load_pct_count;
    uint32_t ts;
    bool has_models;
    bool has_heap_psram_free;
    bool has_heap_psram_min;
    bool has_stream_dropped;
    bool has_q_clean_peak;
    bool has_json_arena_peak;
    bool has_rssi_dbm;
    bool has_core_load_pct;
    bool has_ts;
} heartbeat_t;

/** Fill out from a parsed heartbeat object, checking type, range, enum and size.
 *  @ctx any | non-blocking | out is cleared first; strings are copied, not borrowed
 *  @ret false on the first field outside the contract; out is then partly filled
 */
static inline bool heartbeat_from_json(const cJSON *root, heartbeat_t *out)
{
    if (!cJSON_IsObject(root) || out == NULL) { return false; }
    memset(out, 0, sizeof(*out));
    const cJSON *item = NULL;
    item = cJSON_GetObjectItemCaseSensitive(root, "deviceId");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->device_id)) { return false; }
        strcpy(out->device_id, item->valuestring);
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "fw");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->fw)) { return false; }
        strcpy(out->fw, item->valuestring);
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "models");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->models)) { return false; }
        strcpy(out->models, item->valuestring);
        out->has_models = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "uptimeS");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 4294967295.0) { return false; }
        out->uptime_s = (uint32_t) item->valuedouble;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "heapInternalFree");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 4294967295.0) { return false; }
        out->heap_internal_free = (uint32_t) item->valuedouble;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "heapInternalMin");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 4294967295.0) { return false; }
        out->heap_internal_min = (uint32_t) item->valuedouble;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "heapPsramFree");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 4294967295.0) { return false; }
        out->heap_psram_free = (uint32_t) item->valuedouble;
        out->has_heap_psram_free = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "heapPsramMin");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 4294967295.0) { return false; }
        out->heap_psram_min = (uint32_t) item->valuedouble;
        out->has_heap_psram_min = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "dmaOverflows");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 4294967295.0) { return false; }
        out->dma_overflows = (uint32_t) item->valuedouble;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "framesDropped");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 4294967295.0) { return false; }
        out->frames_dropped = (uint32_t) item->valuedouble;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "cleanDropped");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 4294967295.0) { return false; }
        out->clean_dropped = (uint32_t) item->valuedouble;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "eventsDropped");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 4294967295.0) { return false; }
        out->events_dropped = (uint32_t) item->valuedouble;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "streamDropped");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 4294967295.0) { return false; }
        out->stream_dropped = (uint32_t) item->valuedouble;
        out->has_stream_dropped = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "qCleanPeak");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 65535.0) { return false; }
        out->q_clean_peak = (uint16_t) item->valuedouble;
        out->has_q_clean_peak = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "jsonArenaPeak");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 65535.0) { return false; }
        out->json_arena_peak = (uint16_t) item->valuedouble;
        out->has_json_arena_peak = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "rssiDbm");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < -127.0 || item->valuedouble > 0.0) { return false; }
        out->rssi_dbm = (int8_t) item->valuedouble;
        out->has_rssi_dbm = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "coreLoadPct");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsArray(item) || cJSON_GetArraySize(item) > 2) { return false; }
        if (cJSON_GetArraySize(item) < 0) { return false; }
        const cJSON *el = NULL;
        cJSON_ArrayForEach(el, item) {
            if (!cJSON_IsNumber(el)) { return false; }
            if (el->valuedouble < 0.0 || el->valuedouble > 100.0) { return false; }
            out->core_load_pct[out->core_load_pct_count] = (uint8_t) el->valuedouble;
            out->core_load_pct_count++;
        }
        out->has_core_load_pct = true;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "ts");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 4294967295.0) { return false; }
        out->ts = (uint32_t) item->valuedouble;
        out->has_ts = true;
    }
    return true;
}

/** Build the JSON object of one heartbeat for publishing.
 *  @ctx any | non-blocking | allocates through cJSON: caller frees with cJSON_Delete
 *  @ret NULL when the root object cannot be allocated
 */
static inline cJSON *heartbeat_to_json(const heartbeat_t *in)
{
    if (in == NULL) { return NULL; }
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) { return NULL; }
    cJSON_AddStringToObject(root, "deviceId", in->device_id);
    cJSON_AddStringToObject(root, "fw", in->fw);
    if (in->has_models) {
        cJSON_AddStringToObject(root, "models", in->models);
    }
    cJSON_AddNumberToObject(root, "uptimeS", (double) in->uptime_s);
    cJSON_AddNumberToObject(root, "heapInternalFree", (double) in->heap_internal_free);
    cJSON_AddNumberToObject(root, "heapInternalMin", (double) in->heap_internal_min);
    if (in->has_heap_psram_free) {
        cJSON_AddNumberToObject(root, "heapPsramFree", (double) in->heap_psram_free);
    }
    if (in->has_heap_psram_min) {
        cJSON_AddNumberToObject(root, "heapPsramMin", (double) in->heap_psram_min);
    }
    cJSON_AddNumberToObject(root, "dmaOverflows", (double) in->dma_overflows);
    cJSON_AddNumberToObject(root, "framesDropped", (double) in->frames_dropped);
    cJSON_AddNumberToObject(root, "cleanDropped", (double) in->clean_dropped);
    cJSON_AddNumberToObject(root, "eventsDropped", (double) in->events_dropped);
    if (in->has_stream_dropped) {
        cJSON_AddNumberToObject(root, "streamDropped", (double) in->stream_dropped);
    }
    if (in->has_q_clean_peak) {
        cJSON_AddNumberToObject(root, "qCleanPeak", (double) in->q_clean_peak);
    }
    if (in->has_json_arena_peak) {
        cJSON_AddNumberToObject(root, "jsonArenaPeak", (double) in->json_arena_peak);
    }
    if (in->has_rssi_dbm) {
        cJSON_AddNumberToObject(root, "rssiDbm", (double) in->rssi_dbm);
    }
    if (in->has_core_load_pct) {
        cJSON *core_load_pct_arr = cJSON_AddArrayToObject(root, "coreLoadPct");
        for (int i = 0; core_load_pct_arr != NULL && i < in->core_load_pct_count; i++) {
            cJSON_AddItemToArray(core_load_pct_arr, cJSON_CreateNumber((double) in->core_load_pct[i]));
        }
    }
    if (in->has_ts) {
        cJSON_AddNumberToObject(root, "ts", (double) in->ts);
    }
    return root;
}

typedef enum {
    OTA_MANIFEST_KIND_FIRMWARE = 0,
    OTA_MANIFEST_KIND_MODELS = 1,
    OTA_MANIFEST_KIND_COUNT = 2,
} ota_manifest_kind_t;

/** Contract spelling of a value; "" when it is out of range.
 *  @ctx any | non-blocking | returns a static string
 */
static inline const char *ota_manifest_kind_str(ota_manifest_kind_t v)
{
    switch (v) {
    case OTA_MANIFEST_KIND_FIRMWARE: return "FIRMWARE";
    case OTA_MANIFEST_KIND_MODELS: return "MODELS";
    default: return "";
    }
}

/** Value of a contract spelling.
 *  @ctx any | non-blocking
 *  @ret false for NULL or a spelling the contract does not list
 */
static inline bool ota_manifest_kind_parse(const char *s, ota_manifest_kind_t *out)
{
    if (s == NULL || out == NULL) { return false; }
    if (strcmp(s, "FIRMWARE") == 0) { *out = OTA_MANIFEST_KIND_FIRMWARE; return true; }
    if (strcmp(s, "MODELS") == 0) { *out = OTA_MANIFEST_KIND_MODELS; return true; }
    return false;
}

typedef struct {
    ota_manifest_kind_t kind;
    char version[33];
    char url[257];
    char sha256[65];
    uint32_t size_bytes;
} ota_manifest_t;

/** Fill out from a parsed ota_manifest object, checking type, range, enum and size.
 *  @ctx any | non-blocking | out is cleared first; strings are copied, not borrowed
 *  @ret false on the first field outside the contract; out is then partly filled
 */
static inline bool ota_manifest_from_json(const cJSON *root, ota_manifest_t *out)
{
    if (!cJSON_IsObject(root) || out == NULL) { return false; }
    memset(out, 0, sizeof(*out));
    const cJSON *item = NULL;
    item = cJSON_GetObjectItemCaseSensitive(root, "kind");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || !ota_manifest_kind_parse(item->valuestring, &out->kind)) { return false; }
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "version");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->version)) { return false; }
        strcpy(out->version, item->valuestring);
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "url");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->url)) { return false; }
        strcpy(out->url, item->valuestring);
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "sha256");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->sha256)) { return false; }
        strcpy(out->sha256, item->valuestring);
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "sizeBytes");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 1.0 || item->valuedouble > 4294967295.0) { return false; }
        out->size_bytes = (uint32_t) item->valuedouble;
    }
    return true;
}

/** Build the JSON object of one ota_manifest for publishing.
 *  @ctx any | non-blocking | allocates through cJSON: caller frees with cJSON_Delete
 *  @ret NULL when the root object cannot be allocated
 */
static inline cJSON *ota_manifest_to_json(const ota_manifest_t *in)
{
    if (in == NULL) { return NULL; }
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) { return NULL; }
    cJSON_AddStringToObject(root, "kind", ota_manifest_kind_str(in->kind));
    cJSON_AddStringToObject(root, "version", in->version);
    cJSON_AddStringToObject(root, "url", in->url);
    cJSON_AddStringToObject(root, "sha256", in->sha256);
    cJSON_AddNumberToObject(root, "sizeBytes", (double) in->size_bytes);
    return root;
}

typedef struct {
    char id[33];
    char text[513];
} responses_responses_item_t;

/** Fill out from a parsed responses_responses object, checking type, range, enum and size.
 *  @ctx any | non-blocking | out is cleared first; strings are copied, not borrowed
 *  @ret false on the first field outside the contract; out is then partly filled
 */
static inline bool responses_responses_item_from_json(const cJSON *root, responses_responses_item_t *out)
{
    if (!cJSON_IsObject(root) || out == NULL) { return false; }
    memset(out, 0, sizeof(*out));
    const cJSON *item = NULL;
    item = cJSON_GetObjectItemCaseSensitive(root, "id");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->id)) { return false; }
        strcpy(out->id, item->valuestring);
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "text");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->text)) { return false; }
        strcpy(out->text, item->valuestring);
    }
    return true;
}

/** Build the JSON object of one responses_responses for publishing.
 *  @ctx any | non-blocking | allocates through cJSON: caller frees with cJSON_Delete
 *  @ret NULL when the root object cannot be allocated
 */
static inline cJSON *responses_responses_item_to_json(const responses_responses_item_t *in)
{
    if (in == NULL) { return NULL; }
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) { return NULL; }
    cJSON_AddStringToObject(root, "id", in->id);
    cJSON_AddStringToObject(root, "text", in->text);
    return root;
}

typedef struct {
    uint32_t version;
    responses_responses_item_t responses[64];
    uint8_t responses_count;
} responses_t;

/** Fill out from a parsed responses object, checking type, range, enum and size.
 *  @ctx any | non-blocking | out is cleared first; strings are copied, not borrowed
 *  @ret false on the first field outside the contract; out is then partly filled
 */
static inline bool responses_from_json(const cJSON *root, responses_t *out)
{
    if (!cJSON_IsObject(root) || out == NULL) { return false; }
    memset(out, 0, sizeof(*out));
    const cJSON *item = NULL;
    item = cJSON_GetObjectItemCaseSensitive(root, "version");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 1.0 || item->valuedouble > 2147483647.0) { return false; }
        out->version = (uint32_t) item->valuedouble;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "responses");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsArray(item) || cJSON_GetArraySize(item) > 64) { return false; }
        if (cJSON_GetArraySize(item) < 1) { return false; }
        const cJSON *el = NULL;
        cJSON_ArrayForEach(el, item) {
            if (!responses_responses_item_from_json(el, &out->responses[out->responses_count])) { return false; }
            out->responses_count++;
        }
    }
    return true;
}

/** Build the JSON object of one responses for publishing.
 *  @ctx any | non-blocking | allocates through cJSON: caller frees with cJSON_Delete
 *  @ret NULL when the root object cannot be allocated
 */
static inline cJSON *responses_to_json(const responses_t *in)
{
    if (in == NULL) { return NULL; }
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) { return NULL; }
    cJSON_AddNumberToObject(root, "version", (double) in->version);
    cJSON *responses_arr = cJSON_AddArrayToObject(root, "responses");
    for (int i = 0; responses_arr != NULL && i < in->responses_count; i++) {
        cJSON_AddItemToArray(responses_arr, responses_responses_item_to_json(&in->responses[i]));
    }
    return root;
}

typedef enum {
    STATUS_STATE_ONLINE = 0,
    STATUS_STATE_OFFLINE = 1,
    STATUS_STATE_COUNT = 2,
} status_state_t;

/** Contract spelling of a value; "" when it is out of range.
 *  @ctx any | non-blocking | returns a static string
 */
static inline const char *status_state_str(status_state_t v)
{
    switch (v) {
    case STATUS_STATE_ONLINE: return "ONLINE";
    case STATUS_STATE_OFFLINE: return "OFFLINE";
    default: return "";
    }
}

/** Value of a contract spelling.
 *  @ctx any | non-blocking
 *  @ret false for NULL or a spelling the contract does not list
 */
static inline bool status_state_parse(const char *s, status_state_t *out)
{
    if (s == NULL || out == NULL) { return false; }
    if (strcmp(s, "ONLINE") == 0) { *out = STATUS_STATE_ONLINE; return true; }
    if (strcmp(s, "OFFLINE") == 0) { *out = STATUS_STATE_OFFLINE; return true; }
    return false;
}

typedef struct {
    char device_id[33];
    status_state_t state;
    char fw[33];
    bool has_fw;
} status_t;

/** Fill out from a parsed status object, checking type, range, enum and size.
 *  @ctx any | non-blocking | out is cleared first; strings are copied, not borrowed
 *  @ret false on the first field outside the contract; out is then partly filled
 */
static inline bool status_from_json(const cJSON *root, status_t *out)
{
    if (!cJSON_IsObject(root) || out == NULL) { return false; }
    memset(out, 0, sizeof(*out));
    const cJSON *item = NULL;
    item = cJSON_GetObjectItemCaseSensitive(root, "deviceId");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->device_id)) { return false; }
        strcpy(out->device_id, item->valuestring);
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "state");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || !status_state_parse(item->valuestring, &out->state)) { return false; }
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "fw");
    if (item == NULL) { item = NULL; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->fw)) { return false; }
        strcpy(out->fw, item->valuestring);
        out->has_fw = true;
    }
    return true;
}

/** Build the JSON object of one status for publishing.
 *  @ctx any | non-blocking | allocates through cJSON: caller frees with cJSON_Delete
 *  @ret NULL when the root object cannot be allocated
 */
static inline cJSON *status_to_json(const status_t *in)
{
    if (in == NULL) { return NULL; }
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) { return NULL; }
    cJSON_AddStringToObject(root, "deviceId", in->device_id);
    cJSON_AddStringToObject(root, "state", status_state_str(in->state));
    if (in->has_fw) {
        cJSON_AddStringToObject(root, "fw", in->fw);
    }
    return root;
}

typedef enum {
    TELEMETRY_STATE_LISTEN = 0,
    TELEMETRY_STATE_COMMAND = 1,
    TELEMETRY_STATE_REPLY = 2,
    TELEMETRY_STATE_COUNT = 3,
} telemetry_state_t;

/** Contract spelling of a value; "" when it is out of range.
 *  @ctx any | non-blocking | returns a static string
 */
static inline const char *telemetry_state_str(telemetry_state_t v)
{
    switch (v) {
    case TELEMETRY_STATE_LISTEN: return "LISTEN";
    case TELEMETRY_STATE_COMMAND: return "COMMAND";
    case TELEMETRY_STATE_REPLY: return "REPLY";
    default: return "";
    }
}

/** Value of a contract spelling.
 *  @ctx any | non-blocking
 *  @ret false for NULL or a spelling the contract does not list
 */
static inline bool telemetry_state_parse(const char *s, telemetry_state_t *out)
{
    if (s == NULL || out == NULL) { return false; }
    if (strcmp(s, "LISTEN") == 0) { *out = TELEMETRY_STATE_LISTEN; return true; }
    if (strcmp(s, "COMMAND") == 0) { *out = TELEMETRY_STATE_COMMAND; return true; }
    if (strcmp(s, "REPLY") == 0) { *out = TELEMETRY_STATE_REPLY; return true; }
    return false;
}

typedef struct {
    int16_t doa_deg;
    uint8_t doa_conf;
    bool vad;
    int8_t level_dbfs;
    int8_t gain_db;
} telemetry_samples_item_t;

/** Fill out from a parsed telemetry_samples object, checking type, range, enum and size.
 *  @ctx any | non-blocking | out is cleared first; strings are copied, not borrowed
 *  @ret false on the first field outside the contract; out is then partly filled
 */
static inline bool telemetry_samples_item_from_json(const cJSON *root, telemetry_samples_item_t *out)
{
    if (!cJSON_IsObject(root) || out == NULL) { return false; }
    memset(out, 0, sizeof(*out));
    const cJSON *item = NULL;
    item = cJSON_GetObjectItemCaseSensitive(root, "doaDeg");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < -1.0 || item->valuedouble > 180.0) { return false; }
        out->doa_deg = (int16_t) item->valuedouble;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "doaConf");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 255.0) { return false; }
        out->doa_conf = (uint8_t) item->valuedouble;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "vad");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsBool(item)) { return false; }
        out->vad = cJSON_IsTrue(item);
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "levelDbfs");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < -127.0 || item->valuedouble > 0.0) { return false; }
        out->level_dbfs = (int8_t) item->valuedouble;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "gainDb");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < -40.0 || item->valuedouble > 40.0) { return false; }
        out->gain_db = (int8_t) item->valuedouble;
    }
    return true;
}

/** Build the JSON object of one telemetry_samples for publishing.
 *  @ctx any | non-blocking | allocates through cJSON: caller frees with cJSON_Delete
 *  @ret NULL when the root object cannot be allocated
 */
static inline cJSON *telemetry_samples_item_to_json(const telemetry_samples_item_t *in)
{
    if (in == NULL) { return NULL; }
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) { return NULL; }
    cJSON_AddNumberToObject(root, "doaDeg", (double) in->doa_deg);
    cJSON_AddNumberToObject(root, "doaConf", (double) in->doa_conf);
    cJSON_AddBoolToObject(root, "vad", in->vad);
    cJSON_AddNumberToObject(root, "levelDbfs", (double) in->level_dbfs);
    cJSON_AddNumberToObject(root, "gainDb", (double) in->gain_db);
    return root;
}

typedef struct {
    char device_id[33];
    uint32_t seq;
    telemetry_state_t state;
    telemetry_samples_item_t samples[10];
    uint8_t samples_count;
} telemetry_t;

/** Fill out from a parsed telemetry object, checking type, range, enum and size.
 *  @ctx any | non-blocking | out is cleared first; strings are copied, not borrowed
 *  @ret false on the first field outside the contract; out is then partly filled
 */
static inline bool telemetry_from_json(const cJSON *root, telemetry_t *out)
{
    if (!cJSON_IsObject(root) || out == NULL) { return false; }
    memset(out, 0, sizeof(*out));
    const cJSON *item = NULL;
    item = cJSON_GetObjectItemCaseSensitive(root, "deviceId");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || strlen(item->valuestring) >= sizeof(out->device_id)) { return false; }
        strcpy(out->device_id, item->valuestring);
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "seq");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsNumber(item)) { return false; }
        if (item->valuedouble < 0.0 || item->valuedouble > 4294967295.0) { return false; }
        out->seq = (uint32_t) item->valuedouble;
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "state");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsString(item) || !telemetry_state_parse(item->valuestring, &out->state)) { return false; }
    }
    item = cJSON_GetObjectItemCaseSensitive(root, "samples");
    if (item == NULL) { return false; }
    if (item != NULL) {
        if (!cJSON_IsArray(item) || cJSON_GetArraySize(item) > 10) { return false; }
        if (cJSON_GetArraySize(item) < 1) { return false; }
        const cJSON *el = NULL;
        cJSON_ArrayForEach(el, item) {
            if (!telemetry_samples_item_from_json(el, &out->samples[out->samples_count])) { return false; }
            out->samples_count++;
        }
    }
    return true;
}

/** Build the JSON object of one telemetry for publishing.
 *  @ctx any | non-blocking | allocates through cJSON: caller frees with cJSON_Delete
 *  @ret NULL when the root object cannot be allocated
 */
static inline cJSON *telemetry_to_json(const telemetry_t *in)
{
    if (in == NULL) { return NULL; }
    cJSON *root = cJSON_CreateObject();
    if (root == NULL) { return NULL; }
    cJSON_AddStringToObject(root, "deviceId", in->device_id);
    cJSON_AddNumberToObject(root, "seq", (double) in->seq);
    cJSON_AddStringToObject(root, "state", telemetry_state_str(in->state));
    cJSON *samples_arr = cJSON_AddArrayToObject(root, "samples");
    for (int i = 0; samples_arr != NULL && i < in->samples_count; i++) {
        cJSON_AddItemToArray(samples_arr, telemetry_samples_item_to_json(&in->samples[i]));
    }
    return root;
}

#ifdef __cplusplus
}
#endif
