#include "app_boot.h"

#include <inttypes.h>
#include <math.h>
#include <stdbool.h>

#include "ai_engine.h"
#include "app_wiring.h"
#include "bsp_board.h"
#include "drv_audio.h"
#include "esp_app_desc.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "gen_afe.h"
#include "gen_grid.h"
#include "gen_topics.h"
#include "lang_vi.h"
#include "net_mqtt.h"
#include "sdkconfig.h"
#include "svc_front.h"
#include "storage_format.h"
#include "svc_listen.h"
#include "svc_report.h"
#include "sys_storage.h"

#define DMA_DESC_NUM 8 // 128 ms of hops outlasts one flash erase (KEHOACH 5.5)
#define MODEL_SLOT 0   // models_0, the one model partition (KEHOACH 6.1)

#define APP_SEED_VER GEN_AFE_VERSION // raised in afe.yaml when a seed changes (KEHOACH 6.2)

_Static_assert(sizeof(((dsp_afe_calib_t *)0)->balance) == STORAGE_CALIB_BAL_BYTES,
               "calib/bal is the balance table");

#if CONFIG_APP_SPEAKER_ENABLE
#define SPEAKER_FITTED true
#else
#define SPEAKER_FITTED false
#endif

static const char *TAG = "app_boot";

static void log_heap(const char *step)
{
    ESP_LOGI(TAG, "%-8s internal %u B free, %u B largest block; psram %u B free", step,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
}

static uint8_t pcm_shift(void)
{
    uint8_t shift = 0;
    if (sys_storage_get_u8(STORAGE_NS_CALIB, STORAGE_KEY_PCM_SHIFT, &shift) == ESP_OK) { return shift; }
    ESP_LOGW(TAG, "calib/pcm_shift absent, using %d", CONFIG_APP_PCM_SHIFT_FALLBACK);
    return CONFIG_APP_PCM_SHIFT_FALLBACK;
}

// ai_engine reads the kws keys while it loads, so the seeds go first.
static void seed_operating(void)
{
    const sys_storage_seed_t seeds[] = {
        {STORAGE_NS_AFE, STORAGE_KEY_NS_FLOOR_DB, SYS_STORAGE_I8, lrintf(GEN_AFE_NS_FLOOR_DB)},
        {STORAGE_NS_AFE, STORAGE_KEY_AGC_TARGET_DBFS, SYS_STORAGE_I8, lrintf(GEN_AFE_AGC_TARGET_DBFS)},
        {STORAGE_NS_AFE, STORAGE_KEY_VAD_MODE, SYS_STORAGE_U8, GEN_AFE_VAD_AGGRESSIVENESS},
    };
    const esp_err_t err = sys_storage_seed(APP_SEED_VER, seeds, sizeof(seeds) / sizeof(seeds[0]));
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "seeds not written (%s), falling back to afe.yaml and Kconfig", esp_err_to_name(err));
    }
}

static float afe_i8(const char *key, float fallback)
{
    int8_t value = 0;
    return sys_storage_get_i8(STORAGE_NS_AFE, key, &value) == ESP_OK ? (float)value : fallback;
}

static uint8_t vad_mode(void)
{
    uint8_t mode = 0;
    return sys_storage_get_u8(STORAGE_NS_AFE, STORAGE_KEY_VAD_MODE, &mode) == ESP_OK
               ? mode
               : GEN_AFE_VAD_AGGRESSIVENESS;
}

// Without a balance table of this format the chain runs without balance, which a board never calibrated
// needs.
static const dsp_afe_calib_t *load_calib(void)
{
    static dsp_afe_calib_t calib;
    uint32_t version = 0;
    uint32_t at = 0;
    if (sys_storage_get_u32(STORAGE_NS_CALIB, STORAGE_KEY_BAL_VER, &version) != ESP_OK ||
        version != STORAGE_CALIB_BAL_VERSION ||
        sys_storage_get_blob(STORAGE_NS_CALIB, STORAGE_KEY_BAL, calib.balance, sizeof(calib.balance)) !=
            ESP_OK) {
        ESP_LOGW(TAG, "calib/bal absent or of another format, running without balance");
        return NULL;
    }
    if (sys_storage_get_u32(STORAGE_NS_CALIB, STORAGE_KEY_AEC_DELAY, &calib.aec_delay_samples) != ESP_OK) {
        calib.aec_delay_samples = 0;
    }
    (void)sys_storage_get_u32(STORAGE_NS_CALIB, STORAGE_KEY_BAL_AT, &at);
    ESP_LOGI(TAG, "calib/bal version %" PRIu32 ", written at %" PRIu32 ", aec delay %" PRIu32 " samples",
             version, at, calib.aec_delay_samples);
    return &calib;
}

// The image brings the pair chosen for its command; SET_CONFIG wins until the suggested pair changes
// (KEHOACH 6.2).
static void seed_thresholds(void)
{
    uint16_t reject = CONFIG_SVC_LISTEN_CMD_REJECT_PERMILLE, margin = CONFIG_SVC_LISTEN_CMD_MARGIN_PERMILLE;
    sys_storage_models_t models;
    if (sys_storage_map_models(MODEL_SLOT, &models) == ESP_OK) {
        if (models.header->cmd_reject_permille != 0) {
            reject = models.header->cmd_reject_permille;
            margin = models.header->cmd_margin_permille;
        }
        sys_storage_unmap_models(&models);
    }
    const uint32_t suggested = (uint32_t)reject << 16 | margin;
    uint32_t seeded = 0;
    if (sys_storage_get_u32(STORAGE_NS_KWS, STORAGE_KEY_CMD_SEEDED, &seeded) == ESP_OK &&
        seeded == suggested) {
        return;
    }
    esp_err_t err = sys_storage_set_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_REJECT, reject);
    if (err == ESP_OK) { err = sys_storage_set_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN, margin); }
    if (err == ESP_OK) { err = sys_storage_set_u32(STORAGE_NS_KWS, STORAGE_KEY_CMD_SEEDED, suggested); }
    ESP_LOGI(TAG, "command thresholds %u, %u seeded: %s", (unsigned)reject, (unsigned)margin,
             esp_err_to_name(err));
}

static void load_models(void)
{
    const esp_err_t err = ai_engine_load(MODEL_SLOT);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "no models in " STORAGE_MODEL_LABEL_SLOT0 " (%s), running without them",
                 esp_err_to_name(err));
    }
}

svc_listen_commands_t app_boot_commands(const command_set_t *set, const char **texts, const char **ids)
{
    for (uint8_t c = 0; c < set->commands_count; c++) {
        texts[c] = set->commands[c].text;
        ids[c] = set->commands[c].id;
    }
    return (svc_listen_commands_t){
        .texts = texts, .ids = ids, .n_commands = set->commands_count, .version = set->version};
}

static esp_err_t listen_to(const command_set_t *set)
{
    const char *texts[AI_ENGINE_COMMANDS_MAX];
    const char *ids[AI_ENGINE_COMMANDS_MAX];
    svc_listen_config_t cfg = {
        .commands = app_boot_commands(set, texts, ids),
        .dialects = LANG_VI_DIALECT_ALL,
        .reject_permille = CONFIG_SVC_LISTEN_CMD_REJECT_PERMILLE,
        .margin_permille = CONFIG_SVC_LISTEN_CMD_MARGIN_PERMILLE,
    };
    (void)sys_storage_get_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_REJECT, &cfg.reject_permille);
    (void)sys_storage_get_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN, &cfg.margin_permille);
    const esp_err_t err = svc_listen_init(&cfg);
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "listening: every utterance vad finds, delta1 %u, delta2 %u (KEHOACH 5.4)",
                 (unsigned)cfg.reject_permille, (unsigned)cfg.margin_permille);
    }
    return err;
}

static void start_listener(void)
{
    if (!ai_engine_has(AI_ENGINE_MODEL_COMMAND)) {
        ESP_LOGW(TAG, "no command in the models, not listening");
        return;
    }
    char *text = heap_caps_malloc(NET_MQTT_COMMANDS_TEXT_BYTES, MALLOC_CAP_SPIRAM);
    command_set_t *set = heap_caps_malloc(sizeof(*set), MALLOC_CAP_SPIRAM);
    size_t len = 0;
    esp_err_t err = text != NULL && set != NULL ? sys_storage_read_file(STORAGE_PATH_COMMANDS, text,
                                                                        NET_MQTT_COMMANDS_TEXT_BYTES, &len)
                                                : ESP_ERR_NO_MEM;
    if (err == ESP_OK) { err = net_mqtt_parse_command_set(text, len, set); }
    if (err == ESP_OK) { err = listen_to(set); }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "%s: not listening (%s)", STORAGE_PATH_COMMANDS, esp_err_to_name(err));
    }
    heap_caps_free(text);
    heap_caps_free(set);
}

esp_err_t app_boot(void)
{
    ESP_RETURN_ON_ERROR(bsp_board_init(), TAG, "board");
    ESP_RETURN_ON_ERROR(sys_storage_init(), TAG, "storage");
    log_heap("storage");
    ESP_RETURN_ON_ERROR(app_wiring_init(), TAG, "wiring");
    log_heap("wiring");
    ESP_RETURN_ON_ERROR(net_mqtt_init(), TAG, "json arenas");
    const drv_audio_config_t audio = {
        .pcm_shift = pcm_shift(),
        .enable_tx = SPEAKER_FITTED,
        .dma_desc_num = DMA_DESC_NUM,
    };
    ESP_RETURN_ON_ERROR(drv_audio_init(&audio), TAG, "audio");
    log_heap("audio");
    seed_operating();
    seed_thresholds();
    load_models();
    log_heap("models");
    const svc_front_config_t front = {
        .n_channels = drv_audio_channels(),
        .calib = load_calib(),
        .ns_floor_db = afe_i8(STORAGE_KEY_NS_FLOOR_DB, GEN_AFE_NS_FLOOR_DB),
        .agc_target_dbfs = afe_i8(STORAGE_KEY_AGC_TARGET_DBFS, GEN_AFE_AGC_TARGET_DBFS),
        .vad_aggressiveness = vad_mode(),
    };
    ESP_LOGI(TAG, "afe: ns floor %.0f dB, agc target %.0f dBFS, vad mode %u", (double)front.ns_floor_db,
             (double)front.agc_target_dbfs, (unsigned)front.vad_aggressiveness);
    ESP_RETURN_ON_ERROR(svc_front_init(&front), TAG, "front end");
    log_heap("front");
    start_listener();
    log_heap("listen");
#if CONFIG_NET_STREAM_ENABLE
    const svc_report_stream_config_t stream = {
        .sb = app_wiring()->stream, .system = app_wiring()->system, .raw_channels = drv_audio_channels()};
    ESP_RETURN_ON_ERROR(svc_report_stream_init(&stream), TAG, "stream");
#endif

    char device_id[GEN_TOPIC_DEVICE_ID_MAX + 1];
    ESP_RETURN_ON_ERROR(sys_storage_device_id(device_id, sizeof(device_id)), TAG, "device id");
    ESP_LOGI(TAG, "esp-sr %s, grid 0x%08x, %s, boot %" PRIu32 ", %u channel(s), speaker %s",
             esp_app_get_description()->version, (unsigned)GEN_GRID_HASH, device_id, sys_storage_boot_count(),
             (unsigned)drv_audio_channels(), SPEAKER_FITTED ? "on" : "off");
    return ESP_OK;
}
