#include "app_tasks.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "ai_engine.h"
#include "app_boot.h"
#include "app_err.h"
#include "app_events.h"
#include "app_wiring.h"
#include "drv_audio.h"
#include "dsp_afe.h"
#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_task_wdt.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gen_grid.h"
#include "gen_payload.h"
#include "gen_topics.h"
#include "net_mqtt.h"
#include "net_wifi.h"
#include "sdkconfig.h"
#include "svc_front.h"
#include "svc_listen.h"
#include "svc_report.h"
#include "sys_storage.h"

#define CORE_FRAME 1    // hard 16 ms deadline, nothing else runs here (KEHOACH 5.1)
#define CORE_BUFFERED 0 // Wi-Fi, lwIP and every task a queue shields

#define THU_STACK_BYTES 3072
#define SACH_STACK_BYTES 6144
#define NHAN_STACK_BYTES 8192
#define DIEU_STACK_BYTES 4096
#define NOI_STACK_BYTES 8192
#define GUI_STACK_BYTES 4096
#define NET_STACK_BYTES 4096
#define LUONG_STACK_BYTES 4096

#define THU_PRIORITY 17
#define SACH_PRIORITY 16
#define NHAN_PRIORITY 10
#define DIEU_PRIORITY 8
#define NOI_PRIORITY 5
#define GUI_PRIORITY 4
#define NET_PRIORITY 3
#define LUONG_PRIORITY 3

#define DMA_WAIT_MS (2 * GEN_GRID_HOP_US / 1000)
#define FRAME_WAIT_MS 100
#define CLEAN_WAIT_MS 100
#define DIEU_WAIT_MS 100
#define SPEAK_WAIT_MS 1000
#define GUI_PERIOD_MS 100
#define CRED_POLL_MS 2000
#define LINK_WAIT_MS 1000
#define HEARTBEAT_TICKS (GEN_TOPIC_HEARTBEAT_INTERVAL_S * 1000 / GUI_PERIOD_MS)
#define US_PER_S 1000000
#define US_PER_MS 1000

typedef struct {
    TaskFunction_t entry;
    const char *name;
    StackType_t *stack;
    uint32_t stack_bytes;
    UBaseType_t priority;
    BaseType_t core;
} task_spec_t;

static const char *TAG = "app_tasks";

static int16_t s_drain[APP_FRAME_MAX_SAMPLES];

static void count_in_stats(const app_wiring_t *w, uint32_t *field)
{
    taskENTER_CRITICAL(w->afe_stats_lock);
    (*field)++;
    taskEXIT_CRITICAL(w->afe_stats_lock);
}

static void return_slot(const app_wiring_t *w, uint8_t slot)
{
    const BaseType_t sent = xQueueSend(w->free_slots, &slot, 0);
    // q_free is as deep as the pool, so a slot coming home always fits (KEHOACH 5.3).
    configASSERT(sent == pdTRUE);
    (void)sent;
}

static void thu_task(void *arg)
{
    const app_wiring_t *w = arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    for (;;) {
        uint8_t slot = 0;
        bool have_slot = xQueueReceive(w->free_slots, &slot, 0) == pdTRUE;
        int16_t *dst = have_slot ? w->pool[slot].pcm : s_drain;
        uint32_t seq = 0;
        const esp_err_t err = drv_audio_read_frame(dst, &seq, DMA_WAIT_MS);
        if (err != ESP_OK) {
            if (have_slot) { return_slot(w, slot); }
        } else {
            if (!have_slot && xQueueReceive(w->free_slots, &slot, 0) == pdTRUE) {
                // A slot sach_task returns while the frame is on its way still takes it.
                memcpy(w->pool[slot].pcm, s_drain, sizeof(s_drain));
                have_slot = true;
            }
            if (!have_slot) {
                count_in_stats(w, &w->afe_stats->frames_dropped);
            } else {
                w->pool[slot].seq = seq;
                if (xQueueSend(w->frame, &slot, 0) != pdTRUE) {
                    return_slot(w, slot);
                    count_in_stats(w, &w->afe_stats->frames_dropped);
                }
            }
        }
        esp_task_wdt_reset();
    }
}

static void publish_clean(const app_wiring_t *w, const dsp_afe_frame_t *clean)
{
    // Zero timeout: a full q_clean drops the hop, never stalls core 1 (KEHOACH 5.3).
    const bool queued = xQueueSend(w->clean, clean, 0) == pdTRUE;
    taskENTER_CRITICAL(w->afe_stats_lock);
    w->afe_stats->hops++;
    w->afe_stats->clean_dropped += queued ? 0u : 1u;
    w->afe_stats->doa_deg = clean->doa_deg;
    w->afe_stats->doa_conf = clean->doa_conf;
    w->afe_stats->vad = clean->vad;
    w->afe_stats->level_dbfs = clean->level_dbfs;
    w->afe_stats->gain_db = clean->gain_db;
    w->afe_stats->flags = clean->flags;
    taskEXIT_CRITICAL(w->afe_stats_lock);
}

static void sach_task(void *arg)
{
    const app_wiring_t *w = arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    for (;;) {
        uint8_t slot = 0;
        if (xQueueReceive(w->frame, &slot, pdMS_TO_TICKS(FRAME_WAIT_MS)) == pdTRUE) {
            dsp_afe_frame_t clean;
            const esp_err_t err = svc_front_step(w->pool[slot].pcm, w->pool[slot].seq, &clean);
#if CONFIG_NET_STREAM_ENABLE
            svc_report_stream_push(w->pool[slot].seq, w->pool[slot].pcm, err == ESP_OK ? clean.pcm : NULL);
#endif
            return_slot(w, slot);
            if (err == ESP_OK) { publish_clean(w, &clean); }
        }
        esp_task_wdt_reset();
    }
}

static void send_up(const app_wiring_t *w, const app_event_t *e)
{
    // Zero timeout: nhan_task never waits behind the network (KEHOACH 5.3).
    if (xQueueSend(w->event_up, e, 0) != pdTRUE) { count_in_stats(w, &w->afe_stats->events_dropped); }
}

static void raise_decision(const app_wiring_t *w, const svc_listen_decision_t *d)
{
    const app_event_t *e = &d->event;
    // The event leaves first: the log line can wait on a busy UART.
    send_up(w, e);
    ESP_LOGI(TAG,
             "%s %s score %u margin %u gap %u, hops %" PRIu32 "..%" PRIu32 ", %" PRIu32 " ms work, %" PRIu32
             " ms after the close",
             e->kind == APP_EVT_COMMAND ? "COMMAND" : "REJECT",
             e->kind == APP_EVT_COMMAND ? e->command_id : e->code, (unsigned)e->score_permille,
             (unsigned)e->margin_permille, (unsigned)d->free_gap_permille, d->first_seq, e->seq,
             d->work_us / US_PER_MS, d->close_us / US_PER_MS);
}

static void raise_error(const app_wiring_t *w, uint32_t seq, const char *code, const char *command_id)
{
    app_event_t e = {.kind = APP_EVT_ERROR, .seq = seq, .doa_deg = -1};
    strlcpy(e.code, code, sizeof(e.code));
    if (command_id != NULL) { strlcpy(e.command_id, command_id, sizeof(e.command_id)); }
    send_up(w, &e);
    ESP_LOGW(TAG, "ERROR %s %s at hop %" PRIu32, e.code, e.command_id, seq);
}

// Only while no window is open or waits: each utterance is decided by the set it met (KEHOACH 5.3).
static void take_commands(const app_wiring_t *w, uint32_t seq)
{
    const net_mqtt_commands_t *in = NULL;
    if (svc_listen_busy() || xQueueReceive(w->cmdset, &in, 0) != pdTRUE) { return; }
    if (in->parsed != ESP_OK) {
        raise_error(w, seq,
                    in->parsed == APP_ERR_COMMANDS_INVALID ? APP_CODE_COMMANDS_INVALID
                                                           : APP_CODE_COMMANDS_REFUSED,
                    NULL);
        return;
    }
    if (in->set.version == svc_listen_commands_version()) {
        ESP_LOGI(TAG, "commands v%" PRIu32 " already in use", in->set.version);
        return;
    }
    // Two pointers a command outgrow what nhan_task's stack spares; svc_listen copies what it keeps of them.
    const char **names = heap_caps_malloc(2 * AI_ENGINE_COMMANDS_MAX * sizeof(*names), MALLOC_CAP_SPIRAM);
    if (names == NULL) {
        raise_error(w, seq, APP_CODE_COMMANDS_REFUSED, NULL);
        return;
    }
    const svc_listen_commands_t commands = app_boot_commands(&in->set, names, names + AI_ENGINE_COMMANDS_MAX);
    uint16_t unreadable = 0;
    int64_t began_us = esp_timer_get_time();
    esp_err_t err = svc_listen_set_commands(&commands, &unreadable);
    if (err != ESP_OK) {
        const bool invalid = err == APP_ERR_COMMANDS_INVALID;
        raise_error(w, seq, invalid ? APP_CODE_COMMANDS_INVALID : APP_CODE_COMMANDS_REFUSED,
                    invalid ? commands.ids[unreadable] : NULL);
        heap_caps_free(names);
        return;
    }
    heap_caps_free(names);
    const uint32_t read_ms = (uint32_t)((esp_timer_get_time() - began_us) / US_PER_MS);
    began_us = esp_timer_get_time();
    err = sys_storage_write_file(STORAGE_PATH_COMMANDS, in->text, in->text_bytes);
    const uint32_t write_ms = (uint32_t)((esp_timer_get_time() - began_us) / US_PER_MS);
    ESP_LOGI(TAG, "commands v%" PRIu32 " in use: lang_vi %" PRIu32 " ms, %s %s in %" PRIu32 " ms",
             in->set.version, read_ms, STORAGE_PATH_COMMANDS, esp_err_to_name(err), write_ms);
    if (err != ESP_OK) { raise_error(w, seq, APP_CODE_COMMANDS_NOT_SAVED, NULL); }
}

static void nhan_task(void *arg)
{
    const app_wiring_t *w = arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    uint32_t seq = 0;
    for (;;) {
        dsp_afe_frame_t frame;
        take_commands(w, seq);
        // With window work left a frame is awaited a tick only, core 0's lower tasks' turn (KEHOACH 5.4).
        const TickType_t wait = svc_listen_pending() ? 1 : pdMS_TO_TICKS(CLEAN_WAIT_MS);
        svc_listen_decision_t decision;
        if (xQueueReceive(w->clean, &frame, wait) == pdTRUE) {
            seq = frame.seq;
            const bool gap = (frame.flags & DSP_AFE_FLAG_GAP) != 0;
            if (svc_listen_feed(frame.pcm, frame.seq, frame.vad != 0, gap) == ESP_ERR_NO_MEM) {
                ESP_LOGW(TAG, "window queue full: the utterance opened at hop %" PRIu32 " gets no window",
                         frame.seq);
            }
        } else {
            // Back to back while no frame waits, so a window catches up with a short word (KEHOACH 5.4).
            do {
                if (svc_listen_work(&decision)) { raise_decision(w, &decision); }
            } while (svc_listen_pending() && uxQueueMessagesWaiting(w->clean) == 0);
        }
        esp_task_wdt_reset();
    }
}

static void dieu_task(void *arg)
{
    const app_wiring_t *w = arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    for (;;) {
        app_event_t event;
        if (xQueueReceive(w->dialog, &event, pdMS_TO_TICKS(DIEU_WAIT_MS)) == pdTRUE) { (void)event; }
        device_cmd_t cmd;
        while (xQueueReceive(w->cmd, &cmd, 0) == pdTRUE) {
            (void)cmd;
        }
        esp_task_wdt_reset();
    }
}

#if CONFIG_NET_STREAM_ENABLE
static void luong_task(void *arg)
{
    (void)arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    for (;;) {
        svc_report_luong_step();
        esp_task_wdt_reset();
    }
}
#endif

#if CONFIG_APP_SPEAKER_ENABLE
static void noi_task(void *arg)
{
    const app_wiring_t *w = arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    for (;;) {
        app_speak_req_t req;
        if (xQueueReceive(w->speak, &req, pdMS_TO_TICKS(SPEAK_WAIT_MS)) == pdTRUE) { (void)req; }
        esp_task_wdt_reset();
    }
}
#endif

static void fill_heartbeat(const app_afe_stats_t *afe, heartbeat_t *hb)
{
    drv_audio_stats_t audio;
    net_wifi_stats_t wifi;
    drv_audio_stats(&audio);
    net_wifi_stats(&wifi);
    memset(hb, 0, sizeof(*hb));
    strlcpy(hb->fw, esp_app_get_description()->version, sizeof(hb->fw));
    hb->uptime_s = (uint32_t)(esp_timer_get_time() / US_PER_S);
    hb->heap_internal_free = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    hb->heap_internal_min = heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL);
    hb->heap_psram_free = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    hb->heap_psram_min = heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM);
    hb->has_heap_psram_free = hb->has_heap_psram_min = true;
    hb->dma_overflows = audio.dma_overflows;
    hb->frames_dropped = afe->frames_dropped;
    hb->clean_dropped = afe->clean_dropped;
    hb->events_dropped = afe->events_dropped;
#if CONFIG_NET_STREAM_ENABLE
    hb->stream_dropped = svc_report_stream_dropped();
    hb->has_stream_dropped = true;
#endif
    hb->rssi_dbm = wifi.rssi_dbm;
    hb->has_rssi_dbm = wifi.rssi_dbm != 0;
}

static void event_payload(const app_event_t *e, event_t *out)
{
    static const event_kind_t kKinds[] = {
        [APP_EVT_WAKE] = EVENT_KIND_WAKE,
        [APP_EVT_COMMAND] = EVENT_KIND_COMMAND,
        [APP_EVT_REJECT] = EVENT_KIND_REJECT,
        [APP_EVT_ERROR] = EVENT_KIND_ERROR,
    };
    memset(out, 0, sizeof(*out));
    out->seq = e->seq;
    out->kind = kKinds[e->kind];
    out->score_permille = e->score_permille;
    out->margin_permille = e->margin_permille;
    out->has_score_permille = out->has_margin_permille = e->kind != APP_EVT_ERROR;
    strlcpy(out->command_id, e->command_id, sizeof(out->command_id));
    out->has_command_id = e->command_id[0] != '\0';
    strlcpy(out->code, e->code, sizeof(out->code));
    out->has_code = e->code[0] != '\0';
    out->doa_deg = e->doa_deg;
    out->has_doa_deg = e->doa_deg >= 0;
}

static void send_events(const app_wiring_t *w, bool online)
{
    app_event_t e;
    while (xQueueReceive(w->event_up, &e, 0) == pdTRUE) {
        event_t payload;
        event_payload(&e, &payload);
        if (!online || net_mqtt_publish_event(&payload) != ESP_OK) {
            count_in_stats(w, &w->afe_stats->events_dropped);
        }
    }
}

static void gui_task(void *arg)
{
    const app_wiring_t *w = arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    TickType_t wake = xTaskGetTickCount();
    uint32_t since_heartbeat = 0;
    bool was_online = false;
    for (;;) {
        vTaskDelayUntil(&wake, pdMS_TO_TICKS(GUI_PERIOD_MS));
        app_afe_stats_t snapshot;
        taskENTER_CRITICAL(w->afe_stats_lock);
        snapshot = *w->afe_stats;
        taskEXIT_CRITICAL(w->afe_stats_lock);
        const bool online = (xEventGroupGetBits(w->system) & APP_BIT_MQTT_OK) != 0;
        send_events(w, online);
        since_heartbeat++;
        if (online && (!was_online || since_heartbeat >= HEARTBEAT_TICKS)) {
            heartbeat_t hb;
            fill_heartbeat(&snapshot, &hb);
            net_mqtt_publish_heartbeat(&hb);
            since_heartbeat = 0;
        }
        was_online = online;
        esp_task_wdt_reset();
    }
}

static void net_task(void *arg);

static StackType_t s_thu_stack[THU_STACK_BYTES] __attribute__((aligned(portBYTE_ALIGNMENT)));
static StackType_t s_sach_stack[SACH_STACK_BYTES] __attribute__((aligned(portBYTE_ALIGNMENT)));
static StackType_t s_nhan_stack[NHAN_STACK_BYTES] __attribute__((aligned(portBYTE_ALIGNMENT)));
static StackType_t s_dieu_stack[DIEU_STACK_BYTES] __attribute__((aligned(portBYTE_ALIGNMENT)));
static StackType_t s_gui_stack[GUI_STACK_BYTES] __attribute__((aligned(portBYTE_ALIGNMENT)));
static StackType_t s_net_stack[NET_STACK_BYTES] __attribute__((aligned(portBYTE_ALIGNMENT)));
#if CONFIG_APP_SPEAKER_ENABLE
static StackType_t s_noi_stack[NOI_STACK_BYTES] __attribute__((aligned(portBYTE_ALIGNMENT)));
#endif
#if CONFIG_NET_STREAM_ENABLE
static StackType_t s_luong_stack[LUONG_STACK_BYTES] __attribute__((aligned(portBYTE_ALIGNMENT)));
#endif

static const task_spec_t kTasks[] = {
    {thu_task, "thu_task", s_thu_stack, THU_STACK_BYTES, THU_PRIORITY, CORE_FRAME},
    {sach_task, "sach_task", s_sach_stack, SACH_STACK_BYTES, SACH_PRIORITY, CORE_FRAME},
    {nhan_task, "nhan_task", s_nhan_stack, NHAN_STACK_BYTES, NHAN_PRIORITY, CORE_BUFFERED},
    {dieu_task, "dieu_task", s_dieu_stack, DIEU_STACK_BYTES, DIEU_PRIORITY, CORE_BUFFERED},
#if CONFIG_APP_SPEAKER_ENABLE
    {noi_task, "noi_task", s_noi_stack, NOI_STACK_BYTES, NOI_PRIORITY, CORE_BUFFERED},
#endif
    {gui_task, "gui_task", s_gui_stack, GUI_STACK_BYTES, GUI_PRIORITY, CORE_BUFFERED},
#if CONFIG_NET_STREAM_ENABLE
    {luong_task, "luong_task", s_luong_stack, LUONG_STACK_BYTES, LUONG_PRIORITY, CORE_BUFFERED},
#endif
    {net_task, "net_task", s_net_stack, NET_STACK_BYTES, NET_PRIORITY, CORE_BUFFERED},
};

#define TASK_COUNT (sizeof(kTasks) / sizeof(kTasks[0]))

static StaticTask_t s_tcb[TASK_COUNT];
static TaskHandle_t s_handle[TASK_COUNT];
static uint32_t s_final_free_bytes[TASK_COUNT];
static volatile bool s_finished[TASK_COUNT];

// Found by name: a one-shot task may finish ahead of its creator storing the handle.
static void finish_oneshot(void)
{
    const char *self = pcTaskGetName(NULL);
    for (size_t i = 0; i < TASK_COUNT; i++) {
        if (strcmp(kTasks[i].name, self) == 0) {
            s_final_free_bytes[i] = uxTaskGetStackHighWaterMark(NULL);
            s_finished[i] = true;
        }
    }
    vTaskDelete(NULL);
}

static void net_task(void *arg)
{
    const app_wiring_t *w = arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    char device_id[GEN_TOPIC_DEVICE_ID_MAX + 1];
    const bool named = sys_storage_device_id(device_id, sizeof(device_id)) == ESP_OK;
    esp_err_t err = net_wifi_init(w->system, named ? device_id : NULL);
    bool told = false;
    for (err = err == ESP_OK ? net_wifi_apply() : err; err == ESP_ERR_NOT_FOUND; err = net_wifi_apply()) {
        if (!told) {
            ESP_LOGW(TAG, "no wifi/ssid yet: type wifi set <ssid> <pass> on the console");
            told = true;
        }
        esp_task_wdt_reset();
        vTaskDelay(pdMS_TO_TICKS(CRED_POLL_MS));
    }
    if (err != ESP_OK) { ESP_LOGE(TAG, "wifi: %s, running without a network", esp_err_to_name(err)); }
    while (err == ESP_OK &&
           (xEventGroupWaitBits(w->system, APP_BIT_WIFI_OK, pdFALSE, pdTRUE, pdMS_TO_TICKS(LINK_WAIT_MS)) &
            APP_BIT_WIFI_OK) == 0) {
        esp_task_wdt_reset();
    }
    if (err == ESP_OK) {
        ESP_LOGI(TAG, "link up: internal %u B free, %u B largest block",
                 (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
                 (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));
    }
    const net_mqtt_config_t mqtt = {.device_id = device_id,
                                    .fw_version = esp_app_get_description()->version,
                                    .system = w->system,
                                    .cmdset = w->cmdset};
    told = false;
    for (err = err == ESP_OK && named ? net_mqtt_start(&mqtt) : ESP_ERR_INVALID_STATE;
         err == ESP_ERR_NOT_FOUND; err = net_mqtt_start(&mqtt)) {
        if (!told) {
            ESP_LOGW(TAG, "no device/mqtt_uri yet: nvs set device mqtt_uri mqtt://<broker>:1883");
            told = true;
        }
        esp_task_wdt_reset();
        vTaskDelay(pdMS_TO_TICKS(CRED_POLL_MS));
    }
    if (err != ESP_OK) { ESP_LOGE(TAG, "mqtt: %s, running without a broker", esp_err_to_name(err)); }
    esp_task_wdt_delete(NULL);
    finish_oneshot();
}

esp_err_t app_tasks_start(void)
{
    const app_wiring_t *w = app_wiring();
    if (w == NULL || s_handle[0] != NULL) { return ESP_ERR_INVALID_STATE; }
    uint32_t total_bytes = 0;
    for (size_t i = 0; i < TASK_COUNT; i++) {
        const task_spec_t *t = &kTasks[i];
        s_handle[i] = xTaskCreateStaticPinnedToCore(t->entry, t->name, t->stack_bytes, (void *)w, t->priority,
                                                    t->stack, &s_tcb[i], t->core);
        if (s_handle[i] == NULL) { return ESP_FAIL; }
        total_bytes += t->stack_bytes;
        ESP_LOGI(TAG, "%-10s core %d  prio %2u  stack %5u B", t->name, (int)t->core, (unsigned)t->priority,
                 (unsigned)t->stack_bytes);
    }
    ESP_LOGI(TAG, "%u tasks, %u B of static stack", (unsigned)TASK_COUNT, (unsigned)total_bytes);
    return ESP_OK;
}

void app_tasks_log_watermarks(void)
{
    uint32_t free_bytes[TASK_COUNT];
    bool finished[TASK_COUNT];
    // No task of this core may finish between the flag and the read; logging waits for the resume.
    vTaskSuspendAll();
    for (size_t i = 0; i < TASK_COUNT; i++) {
        finished[i] = s_finished[i];
        free_bytes[i] = finished[i] ? s_final_free_bytes[i] : uxTaskGetStackHighWaterMark(s_handle[i]);
    }
    xTaskResumeAll();
    for (size_t i = 0; i < TASK_COUNT; i++) {
        ESP_LOGI(TAG, "%-10s %5u of %5u B never used%s", kTasks[i].name, (unsigned)free_bytes[i],
                 (unsigned)kTasks[i].stack_bytes, finished[i] ? " (finished)" : "");
    }
}
