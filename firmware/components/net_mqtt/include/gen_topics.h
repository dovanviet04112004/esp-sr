// GENERATED FILE - DO NOT EDIT.
// Source: contracts/mqtt_topics.yaml
// Regenerate: python3 tools/gen_contracts.py

#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

#define GEN_TOPIC_DEVICE_ID_MAX 32
#define GEN_TOPIC_MAX_LEN 49
#define GEN_TOPIC_HEARTBEAT_INTERVAL_S 30
#define GEN_TOPIC_TELEMETRY_PERIOD_MS 1000
#define GEN_TOPIC_TELEMETRY_SAMPLES 10

typedef enum {
    GEN_TOPIC_STATUS = 0,
    GEN_TOPIC_HEARTBEAT = 1,
    GEN_TOPIC_TELEMETRY = 2,
    GEN_TOPIC_EVENT = 3,
    GEN_TOPIC_CMD = 4,
    GEN_TOPIC_COMMANDS = 5,
    GEN_TOPIC_OTA = 6,
    GEN_TOPIC_COUNT = 7,
    GEN_TOPIC_NONE = GEN_TOPIC_COUNT,
} gen_topic_id_t;

typedef struct {
    const char *prefix;
    const char *suffix;
    uint8_t qos;
    bool retain;
    bool up;
    bool will;
} gen_topic_info_t;

static const gen_topic_info_t GEN_TOPIC_INFO[GEN_TOPIC_COUNT] = {
    [GEN_TOPIC_STATUS] = {"sr/", "/up/status", 1, true, true, true},
    [GEN_TOPIC_HEARTBEAT] = {"sr/", "/up/heartbeat", 0, false, true, false},
    [GEN_TOPIC_TELEMETRY] = {"sr/", "/up/telemetry", 0, false, true, false},
    [GEN_TOPIC_EVENT] = {"sr/", "/up/event", 1, false, true, false},
    [GEN_TOPIC_CMD] = {"sr/", "/down/cmd", 1, false, false, false},
    [GEN_TOPIC_COMMANDS] = {"sr/", "/down/commands", 1, true, false, false},
    [GEN_TOPIC_OTA] = {"sr/", "/down/ota", 1, false, false, false},
};

static inline bool gen_topic_build(gen_topic_id_t id, const char *device_id, char *out, size_t cap)
{
    if (id >= GEN_TOPIC_COUNT || device_id == NULL || out == NULL) { return false; }
    const size_t id_len = strlen(device_id);
    if (id_len == 0 || id_len > GEN_TOPIC_DEVICE_ID_MAX) { return false; }
    const size_t pre = strlen(GEN_TOPIC_INFO[id].prefix);
    const size_t suf = strlen(GEN_TOPIC_INFO[id].suffix);
    if (cap < pre + id_len + suf + 1) { return false; }
    memcpy(out, GEN_TOPIC_INFO[id].prefix, pre);
    memcpy(out + pre, device_id, id_len);
    memcpy(out + pre + id_len, GEN_TOPIC_INFO[id].suffix, suf + 1);
    return true;
}

static inline gen_topic_id_t gen_topic_match(const char *topic, size_t len, const char *device_id)
{
    if (topic == NULL || device_id == NULL) { return GEN_TOPIC_NONE; }
    const size_t id_len = strlen(device_id);
    for (int id = 0; id < GEN_TOPIC_COUNT; id++) {
        const size_t pre = strlen(GEN_TOPIC_INFO[id].prefix);
        const size_t suf = strlen(GEN_TOPIC_INFO[id].suffix);
        if (len != pre + id_len + suf) { continue; }
        if (memcmp(topic, GEN_TOPIC_INFO[id].prefix, pre) != 0) { continue; }
        if (memcmp(topic + pre, device_id, id_len) != 0) { continue; }
        if (memcmp(topic + pre + id_len, GEN_TOPIC_INFO[id].suffix, suf) == 0) { return (gen_topic_id_t) id; }
    }
    return GEN_TOPIC_NONE;
}

static inline bool gen_topic_status(const char *device_id, char *out, size_t cap)
{
    return gen_topic_build(GEN_TOPIC_STATUS, device_id, out, cap);
}

static inline bool gen_topic_heartbeat(const char *device_id, char *out, size_t cap)
{
    return gen_topic_build(GEN_TOPIC_HEARTBEAT, device_id, out, cap);
}

static inline bool gen_topic_telemetry(const char *device_id, char *out, size_t cap)
{
    return gen_topic_build(GEN_TOPIC_TELEMETRY, device_id, out, cap);
}

static inline bool gen_topic_event(const char *device_id, char *out, size_t cap)
{
    return gen_topic_build(GEN_TOPIC_EVENT, device_id, out, cap);
}

static inline bool gen_topic_cmd(const char *device_id, char *out, size_t cap)
{
    return gen_topic_build(GEN_TOPIC_CMD, device_id, out, cap);
}

static inline bool gen_topic_commands(const char *device_id, char *out, size_t cap)
{
    return gen_topic_build(GEN_TOPIC_COMMANDS, device_id, out, cap);
}

static inline bool gen_topic_ota(const char *device_id, char *out, size_t cap)
{
    return gen_topic_build(GEN_TOPIC_OTA, device_id, out, cap);
}

#ifdef __cplusplus
}
#endif
