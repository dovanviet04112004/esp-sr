#include <stdio.h>
#include <string.h>

#include "app_events.h"
#include "esp_heap_caps.h"
#include "esp_netif.h"
#include "esp_rom_crc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "gen_array.h"
#include "gen_grid.h"
#include "job_format.h"
#include "lwip/sockets.h"
#include "net_wifi.h"
#include "sdkconfig.h"
#include "sys_storage.h"
#include "test_report.h"
#include "variants.h"

_Static_assert(ESPSR_JOB_REFUSED == ESP_ERR_NOT_SUPPORTED, "items.py reads the refusal from job_format.h");

#define REPORT_TAG "ESPSR"
#define REPORT_LINES_MAX 512 // every item's variants and a few lines more
#define HOSTNAME "espsr-compare"
#define LINK_WAIT_MS 1000
#define ANNOUNCE_S 2 // the address repeats this often until the PC connects
#define SESSION_STACK_BYTES 16384
#define SESSION_PRIORITY 5
#define SESSION_CORE 1 // where the product runs dsp_afe (KEHOACH 5.2)
#define STA_IFKEY "WIFI_STA_DEF"

static bool recv_all(int sock, void *buf, size_t bytes)
{
    uint8_t *at = buf;
    while (bytes > 0) {
        const int got = recv(sock, at, bytes, 0);
        if (got <= 0) { return false; }
        at += got;
        bytes -= (size_t)got;
    }
    return true;
}

static bool send_all(int sock, const void *buf, size_t bytes)
{
    const uint8_t *at = buf;
    while (bytes > 0) {
        const int sent = send(sock, at, bytes, 0);
        if (sent <= 0) { return false; }
        at += sent;
        bytes -= (size_t)sent;
    }
    return true;
}

// The next variant starts once the PC has taken the output, so no transfer runs under its measurement.
static bool await_ack(int sock)
{
    uint8_t ack = 0;
    return recv_all(sock, &ack, sizeof(ack)) && ack == ESPSR_JOB_ACK;
}

static bool job_sound(const espsr_job_t *job)
{
    if (job->magic != ESPSR_JOB_MAGIC || job->version != ESPSR_JOB_VERSION ||
        job->channels != GEN_ARRAY_N_MICS || job->samples == 0 || job->n_variants > ESPSR_JOB_VARIANTS_MAX) {
        return false;
    }
    for (uint32_t i = 0; i < job->n_variants; i++) {
        const espsr_job_variant_t *v = &job->variants[i];
        const bool mono = v->kind == ESPSR_JOB_KIND_WEBRTC || v->kind == ESPSR_JOB_KIND_NSNET;
        const bool fed = mono ? v->source >= 0 && (uint32_t)v->source < i : v->source == ESPSR_JOB_NO_SOURCE;
        if (v->kind > ESPSR_JOB_KIND_NSNET || !fed) { return false; }
    }
    return true;
}

static esp_err_t run_variant(const espsr_job_t *job, uint32_t i, const int16_t *input,
                             int16_t *const *outputs, espsr_job_result_t *r)
{
    const espsr_job_variant_t *v = &job->variants[i];
    const int16_t *mono = v->source >= 0 ? outputs[v->source] : NULL;
    switch (v->kind) {
    case ESPSR_JOB_KIND_DSP_AFE: return espsr_run_dsp_afe(job, v, input, outputs[i], r);
    case ESPSR_JOB_KIND_BSS: return espsr_run_bss(job, v, input, outputs[i], r);
    case ESPSR_JOB_KIND_WEBRTC: return espsr_run_webrtc(v, mono, job->samples, outputs[i], r);
    default: return espsr_run_nsnet(v, mono, job->samples, outputs[i], r);
    }
}

// The last variant reading each output, so a source is freed as soon as nothing needs it.
static void last_uses(const espsr_job_t *job, int32_t *last)
{
    for (uint32_t i = 0; i < job->n_variants; i++) {
        last[i] = (int32_t)i;
        for (uint32_t j = i + 1; j < job->n_variants; j++) {
            if (job->variants[j].source == (int32_t)i) { last[i] = (int32_t)j; }
        }
    }
}

static void report_result(const espsr_job_variant_t *v, const espsr_job_result_t *r)
{
    test_report_line("variant %.*s status=%d samples=%lu us_mean=%lu us_peak=%lu internal=%lu psram=%lu "
                     "channel=%ld output_crc=%08lx",
                     (int)ESPSR_JOB_NAME_BYTES, v->name, (int)r->status, (unsigned long)r->samples,
                     (unsigned long)r->us_mean, (unsigned long)r->us_peak, (unsigned long)r->internal_bytes,
                     (unsigned long)r->psram_bytes, (long)r->channel, (unsigned long)r->crc32);
}

static bool serve_job(int sock, const espsr_job_t *job)
{
    const size_t samples = job->samples;
    const size_t output_bytes = samples * sizeof(int16_t);
    const size_t input_bytes = samples * job->channels * sizeof(int16_t);
    int16_t *input = heap_caps_malloc(input_bytes, MALLOC_CAP_SPIRAM);
    if (input == NULL || !recv_all(sock, input, input_bytes)) {
        heap_caps_free(input);
        return false;
    }
    test_report_line("job %lu item %.*s samples=%lu variants=%lu", (unsigned long)job->job,
                     (int)ESPSR_JOB_NAME_BYTES, job->item, (unsigned long)samples,
                     (unsigned long)job->n_variants);
    int16_t *outputs[ESPSR_JOB_VARIANTS_MAX] = {0};
    int32_t last[ESPSR_JOB_VARIANTS_MAX];
    last_uses(job, last);
    bool link = true;
    for (uint32_t i = 0; i < job->n_variants && link; i++) {
        const espsr_job_variant_t *v = &job->variants[i];
        espsr_job_result_t r = {.magic = ESPSR_RESULT_MAGIC, .job = job->job, .index = i, .channel = -1};
        const bool fed = v->source < 0 || outputs[v->source] != NULL;
        outputs[i] = fed ? heap_caps_malloc(output_bytes, MALLOC_CAP_SPIRAM) : NULL;
        r.status = !fed ? ESP_ERR_INVALID_STATE : (outputs[i] == NULL ? ESP_ERR_NO_MEM : ESP_OK);
        if (r.status == ESP_OK) { r.status = run_variant(job, i, input, outputs, &r); }
        if (r.status == ESP_OK) { r.crc32 = esp_rom_crc32_le(0, (const uint8_t *)outputs[i], output_bytes); }
        report_result(v, &r);
        link = send_all(sock, &r, sizeof(r)) &&
               (r.status != ESP_OK || send_all(sock, outputs[i], output_bytes)) && await_ack(sock);
        if (r.status != ESP_OK && outputs[i] != NULL) {
            heap_caps_free(outputs[i]);
            outputs[i] = NULL;
        }
        for (uint32_t k = 0; k <= i; k++) {
            if (outputs[k] != NULL && last[k] <= (int32_t)i) {
                heap_caps_free(outputs[k]);
                outputs[k] = NULL;
            }
        }
    }
    for (uint32_t k = 0; k < job->n_variants; k++) {
        heap_caps_free(outputs[k]);
    }
    heap_caps_free(input);
    return link;
}

static int wait_for_pc(int listener)
{
    esp_netif_ip_info_t ip = {0};
    esp_netif_get_ip_info(esp_netif_get_handle_from_ifkey(STA_IFKEY), &ip);
    for (;;) {
        // Plain console, not the report: the PC reads the address as it comes, and it repeats until then.
        printf("ESPSR listen " IPSTR ":%d\n", IP2STR(&ip.ip), CONFIG_ESPSR_COMPARE_PORT);
        fd_set ready;
        FD_ZERO(&ready);
        FD_SET(listener, &ready);
        struct timeval wait = {.tv_sec = ANNOUNCE_S};
        if (select(listener + 1, &ready, NULL, NULL, &wait) > 0) { return accept(listener, NULL, NULL); }
    }
}

static int open_listener(void)
{
    const int listener = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    const struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_port = htons(CONFIG_ESPSR_COMPARE_PORT),
        .sin_addr.s_addr = htonl(INADDR_ANY),
    };
    if (listener < 0 || bind(listener, (const struct sockaddr *)&addr, sizeof(addr)) != 0 ||
        listen(listener, 1) != 0) {
        return -1;
    }
    return listener;
}

static void session_task(void *arg)
{
    (void)arg;
    const int listener = open_listener();
    espsr_job_t *job = heap_caps_malloc(sizeof(*job), MALLOC_CAP_SPIRAM);
    const int sock = listener >= 0 && job != NULL ? wait_for_pc(listener) : -1;
    unsigned served = 0;
    while (sock >= 0) {
        if (!recv_all(sock, job, sizeof(*job))) {
            test_report_line("link lost after %u jobs", served);
            break;
        }
        if (job->magic == ESPSR_JOB_MAGIC && job->n_variants == 0) { break; }
        if (!job_sound(job)) {
            test_report_line("unsound job %lu", (unsigned long)job->job);
            break;
        }
        if (!serve_job(sock, job)) {
            test_report_line("link lost in job %lu", (unsigned long)job->job);
            break;
        }
        served++;
    }
    if (sock >= 0) { close(sock); }
    if (listener >= 0) { close(listener); }
    test_report_line("done %u jobs", served);
    test_report_serve();
    vTaskDelete(NULL);
}

void app_main(void)
{
    test_report_begin(REPORT_TAG, REPORT_LINES_MAX);
    esp_err_t err = sys_storage_init();
    EventGroupHandle_t system = xEventGroupCreate();
    if (err == ESP_OK) { err = system != NULL ? net_wifi_init(system, HOSTNAME) : ESP_ERR_NO_MEM; }
    if (err == ESP_OK) { err = net_wifi_apply(); }
    if (err != ESP_OK) {
        test_report_line("wifi %s; set wifi/ssid and wifi/pass from the console of main first",
                         esp_err_to_name(err));
        test_report_serve();
        return;
    }
    while ((xEventGroupWaitBits(system, APP_BIT_WIFI_OK, pdFALSE, pdTRUE, pdMS_TO_TICKS(LINK_WAIT_MS)) &
            APP_BIT_WIFI_OK) == 0) {}
    net_wifi_set_low_latency(true);
    if (espsr_models_load() != ESP_OK) { test_report_line("model none in the model partition"); }
    xTaskCreatePinnedToCore(session_task, "espsr_task", SESSION_STACK_BYTES, NULL, SESSION_PRIORITY, NULL,
                            SESSION_CORE);
}
