/** Types that cross the queues of KEHOACH 5.3 and the eg_system bits; handles live in main/app_wiring.c.
 *  Only what can be declared without knowing any layer above L0 belongs here (KEHOACH 4.5.4 rule 2).
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define APP_EVT_ID_MAX_BYTES 33
#define APP_EVT_CODE_MAX_BYTES 33
#define APP_SPEAK_TEXT_MAX_BYTES 513

typedef enum {
    APP_BIT_WIFI_OK = 1u << 0,
    APP_BIT_MQTT_OK = 1u << 1,
    APP_BIT_TIME_OK = 1u << 2,
    APP_BIT_MODELS_OK = 1u << 3,
    APP_BIT_STREAM_ON = 1u << 4,
    APP_BIT_SPEAKING = 1u << 5,
    APP_BIT_CALIBRATING = 1u << 6,
    APP_BIT_OTA_RUNNING = 1u << 7,
} app_system_bit_t;

typedef enum {
    APP_STATE_LISTEN = 0, // NGHE in KEHOACH 5.4
    APP_STATE_COMMAND,    // LENH
    APP_STATE_REPLY,      // DAP
} app_state_t;

typedef enum {
    APP_EVT_WAKE = 0,
    APP_EVT_COMMAND,
    APP_EVT_REJECT,
    APP_EVT_ERROR,
} app_event_kind_t;

typedef struct {
    app_event_kind_t kind;
    uint32_t seq; // frame seq the decision rests on
    uint16_t score_permille;
    uint16_t margin_permille;
    int16_t doa_deg; // -1 when unknown
    char command_id[APP_EVT_ID_MAX_BYTES];
    char code[APP_EVT_CODE_MAX_BYTES]; // upper-case code, e.g. LOW_MARGIN
} app_event_t;

typedef struct {
    char response_id[APP_EVT_ID_MAX_BYTES]; // empty when text is set
    char text[APP_SPEAK_TEXT_MAX_BYTES];    // UTF-8, from SPEAK
} app_speak_req_t;

#ifdef __cplusplus
}
#endif
