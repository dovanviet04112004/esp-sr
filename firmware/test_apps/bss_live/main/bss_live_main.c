#include <inttypes.h>
#include <math.h>
#include <stdatomic.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "app_events.h"
#include "bsp_board.h"
#include "driver/uart.h"
#include "driver/uart_vfs.h"
#include "drv_audio.h"
#include "dsp_afe.h"
#include "dsp_afe/balance.h"
#include "dsp_afe/bss.h"
#include "dsp_afe/doa.h"
#include "dsp_afe/hpf.h"
#include "dsp_afe/ns.h"
#include "dsp_afe/vad.h"
#include "dsp_spec/fft.h"
#include "dsp_spec/stft.h"
#include "esp_app_desc.h"
#include "esp_check.h"
#include "esp_cpu.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_rom_sys.h"
#include "esp_task_wdt.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/stream_buffer.h"
#include "freertos/task.h"
#include "gen_afe.h"
#include "gen_array.h"
#include "gen_grid.h"
#include "gen_stream.h"
#include "net_wifi.h"
#include "sdkconfig.h"
#include "storage_format.h"
#include "svc_report.h"
#include "sys_storage.h"
#include "test_report.h"

#define REPORT_TAG "BSSLIVE"
#define N_MICS GEN_ARRAY_N_MICS
#define SEATS_MAX DSP_AFE_BSS_SEATS_MAX
#define OUT_CHANNELS_MAX (N_MICS + SEATS_MAX)
#define RAW_CHANNELS_MAX (N_MICS + 1) // the speaker reference, on a board that has one
#define PCM_FULL_SCALE 32768.0f
#define SACH_CORE 1 // the hop deadline owns core 1, as in main (KEHOACH 5.1)
#define LUONG_CORE 0
#define SACH_PRIORITY 17
#define LUONG_PRIORITY 3
#define SACH_STACK_BYTES 4096
#define LUONG_STACK_BYTES 4096
#define DMA_DESC_NUM 8
#define DMA_WAIT_MS (2 * GEN_GRID_HOP_US / 1000)
#define LINK_WAIT_MS 1000
#define STATUS_PERIOD_MS 1000
#define DRAIN_WAIT_MS 5000
#define DRAIN_POLL_MS 50
#define STREAM_SLACK_S 30
#define SECONDS_MIN 5
#define SEATS_MIN 2 // bss holds two seats at least
#define MS_PER_S 1000
#define BYTES_PER_KB 1024
#define JOB_BYTES 224
#define HOST_BYTES 64
#define DEVICE_ID_BYTES 33 // deviceId of KEHOACH 6.2: at most 32 characters
#define IP_BYTES 16
#define UART_RX_BYTES 1024
#define REPORT_SPARE_LINES 16
#define PORT_MAX 65535
#define STOP_LINE "stop"

typedef enum {
    NS_OFF = 0,
    NS_MEAN = 1, // once on the mean, ahead of the gates
    NS_EACH = 2, // on every seat after its gate, as dsp_afe places ns
} ns_place_t;

static const char *const NS_NAMES[] = {"off", "mean", "each"};
static const char *const MODE_NAMES[] = {"broadside", "learn"};

typedef struct {
    dsp_afe_bss_mode_t mode;
    uint8_t seats;
    ns_place_t ns;
    uint32_t seconds;
    uint32_t delay_ms; // the output trails its input by this much, 0 for live
    char host[HOST_BYTES];
    uint16_t port;
} job_t;

static const char *TAG = "bss_live";

// sach_task alone touches the chain once s_running is set; app_main builds it ahead of that.
static struct {
    job_t job;
    uint8_t raw_channels;
    uint8_t out_channels;
    uint32_t hops_total;
    uint32_t hop;
    uint32_t delay_hops;
    uint32_t back_hops;                            // the delay less the lookahead
    dsp_spec_cplx_t (*late_mean)[GEN_GRID_N_BINS]; // PSRAM ring, slot hop % delay_hops
    int16_t (*late_raw)[GEN_GRID_HOP_SAMPLES * N_MICS];
    bool heard;    // raw decision of vad_in on the last hop
    bool stopping; // stop is taken: the run ends once the ring is out
    bool in_psram;
    const dsp_afe_calib_t *calib;
    dsp_spec_fft_t *fft;
    dsp_spec_stft_t *analysis[N_MICS];
    dsp_spec_istft_t *synthesis[SEATS_MAX];
    dsp_afe_hpf_t *hpf;
    dsp_afe_doa_t *doa;
    dsp_afe_vad_t *vad_in;
    dsp_afe_bss_t *bss;
    const dsp_afe_ns_ops_t *ns;
    dsp_afe_ns_omlsa_config_t omlsa;
    void *ns_state[SEATS_MAX];
    float pcm[N_MICS][GEN_GRID_HOP_SAMPLES];
    float mics_mean[GEN_GRID_HOP_SAMPLES];
    float power[GEN_GRID_N_BINS];
    float gain[GEN_GRID_N_BINS];
    float synth[GEN_GRID_HOP_SAMPLES];
    dsp_spec_cplx_t bins[N_MICS][GEN_GRID_N_BINS];
    dsp_spec_cplx_t mean[GEN_GRID_N_BINS];
    dsp_spec_cplx_t seat[GEN_GRID_N_BINS];
    dsp_spec_cplx_t kept[GEN_GRID_N_BINS];
    dsp_spec_cplx_t rest[GEN_GRID_N_BINS];
    int16_t raw[GEN_GRID_HOP_SAMPLES * RAW_CHANNELS_MAX];
    int16_t out[GEN_GRID_HOP_SAMPLES * OUT_CHANNELS_MAX];
} s_chain;

// sach_task writes under s_lock, app_main reads under it each second.
static struct {
    uint32_t hops;
    uint32_t window_hops;
    uint64_t window_cycles;
    uint32_t window_peak;
    uint64_t total_cycles;
    uint32_t total_peak;
    uint8_t seats;
    float gate[SEATS_MAX];
    int16_t angle_deg[SEATS_MAX];
    uint32_t heard_end; // hops recorded, then hops_total once the ring is out
    uint32_t hops_total;
} s_stats;

static portMUX_TYPE s_lock = portMUX_INITIALIZER_UNLOCKED;
static atomic_bool s_running;
static atomic_bool s_stop_asked;
static TaskHandle_t s_waiter;
static StreamBufferHandle_t s_sb;

static uint8_t pcm_shift(void)
{
    uint8_t shift = 0;
    if (sys_storage_get_u8(STORAGE_NS_CALIB, STORAGE_KEY_PCM_SHIFT, &shift) == ESP_OK) { return shift; }
    ESP_LOGW(TAG, "calib/pcm_shift absent, using %d", CONFIG_BSS_LIVE_PCM_SHIFT_FALLBACK);
    return CONFIG_BSS_LIVE_PCM_SHIFT_FALLBACK;
}

static uint8_t vad_mode(void)
{
    uint8_t mode = 0;
    return sys_storage_get_u8(STORAGE_NS_AFE, STORAGE_KEY_VAD_MODE, &mode) == ESP_OK
               ? mode
               : GEN_AFE_VAD_AGGRESSIVENESS;
}

static float ns_floor_db(void)
{
    int8_t floor_db = 0;
    return sys_storage_get_i8(STORAGE_NS_AFE, STORAGE_KEY_NS_FLOOR_DB, &floor_db) == ESP_OK
               ? (float)floor_db
               : GEN_AFE_NS_FLOOR_DB;
}

// The product's balance table, or NULL, as main's app_boot reads it.
static const dsp_afe_calib_t *load_calib(void)
{
    static dsp_afe_calib_t calib;
    uint32_t version = 0;
    if (sys_storage_get_u32(STORAGE_NS_CALIB, STORAGE_KEY_BAL_VER, &version) != ESP_OK ||
        version != STORAGE_CALIB_BAL_VERSION ||
        sys_storage_get_blob(STORAGE_NS_CALIB, STORAGE_KEY_BAL, calib.balance, sizeof(calib.balance)) !=
            ESP_OK) {
        return NULL;
    }
    return &calib;
}

static bool parse_u32(const char *text, uint32_t low, uint32_t high, uint32_t *out)
{
    char *tail = NULL;
    const unsigned long value = strtoul(text, &tail, 10);
    if (tail == text || *tail != '\0' || value < low || value > high) { return false; }
    *out = (uint32_t)value;
    return true;
}

static bool parse_field(job_t *job, const char *key, const char *value, uint32_t *seen)
{
    uint32_t number = 0;
    if (strcmp(key, "mode") == 0) {
        const bool learn = strcmp(value, MODE_NAMES[DSP_AFE_BSS_LEARN]) == 0;
        if (!learn && strcmp(value, MODE_NAMES[DSP_AFE_BSS_FIXED]) != 0) { return false; }
        job->mode = learn ? DSP_AFE_BSS_LEARN : DSP_AFE_BSS_FIXED;
    } else if (strcmp(key, "seats") == 0 && parse_u32(value, SEATS_MIN, SEATS_MAX, &number)) {
        job->seats = (uint8_t)number;
    } else if (strcmp(key, "ns") == 0) {
        size_t i = 0;
        while (i < sizeof(NS_NAMES) / sizeof(NS_NAMES[0]) && strcmp(value, NS_NAMES[i]) != 0) {
            i++;
        }
        if (i == sizeof(NS_NAMES) / sizeof(NS_NAMES[0])) { return false; }
        job->ns = (ns_place_t)i;
    } else if (strcmp(key, "seconds") == 0 &&
               parse_u32(value, SECONDS_MIN, CONFIG_BSS_LIVE_SECONDS_MAX, &number)) {
        job->seconds = number;
    } else if (strcmp(key, "delay_ms") == 0 &&
               parse_u32(value, 0, CONFIG_BSS_LIVE_DELAY_MAX_S * MS_PER_S, &number)) {
        job->delay_ms = number;
    } else if (strcmp(key, "host") == 0 && value[0] != '\0' && strlen(value) < HOST_BYTES) {
        strlcpy(job->host, value, sizeof(job->host));
    } else if (strcmp(key, "port") == 0 && parse_u32(value, 1, PORT_MAX, &number)) {
        job->port = (uint16_t)number;
    } else {
        return false;
    }
    (*seen)++;
    return true;
}

// "job mode=<broadside|learn> seats=<n> ns=<off|mean|each> seconds=<n> delay_ms=<n> host=<ip> port=<n>"
static bool parse_job(char *line, job_t *job)
{
    static const uint32_t FIELDS = 7;
    char *save = NULL;
    const char *verb = strtok_r(line, " \r\n", &save);
    if (verb == NULL || strcmp(verb, "job") != 0) { return false; }
    uint32_t seen = 0;
    for (char *word = strtok_r(NULL, " \r\n", &save); word != NULL; word = strtok_r(NULL, " \r\n", &save)) {
        char *value = strchr(word, '=');
        if (value == NULL) { return false; }
        *value++ = '\0';
        if (!parse_field(job, word, value, &seen)) { return false; }
    }
    return seen == FIELDS && (job->mode == DSP_AFE_BSS_LEARN || job->seats == SEATS_MIN);
}

// The console keeps a UART driver from the job to the run's end, for stop; test_report_serve reinstalls it.
static void read_job(job_t *job)
{
    ESP_ERROR_CHECK(uart_driver_install(CONFIG_ESP_CONSOLE_UART_NUM, UART_RX_BYTES, 0, 0, NULL, 0));
    uart_vfs_dev_use_driver(CONFIG_ESP_CONSOLE_UART_NUM);
    char line[JOB_BYTES];
    for (;;) {
        if (fgets(line, sizeof(line), stdin) == NULL) {
            vTaskDelay(1);
            continue;
        }
        if (strncmp(line, "job ", strlen("job ")) != 0) { continue; }
        if (parse_job(line, job)) { break; }
        test_report_line("refused: a job names mode, seats, ns, seconds, delay_ms, host and port once each");
    }
}

static void release_console(void)
{
    uart_vfs_dev_use_nonblocking(CONFIG_ESP_CONSOLE_UART_NUM);
    ESP_ERROR_CHECK(uart_driver_delete(CONFIG_ESP_CONSOLE_UART_NUM));
}

// Whether a whole "stop" line has come in on the console since the last call; never waits.
static bool stop_typed(void)
{
    static char line[JOB_BYTES];
    static size_t used;
    uint8_t byte = 0;
    while (uart_read_bytes(CONFIG_ESP_CONSOLE_UART_NUM, &byte, 1, 0) == 1) {
        if (byte != '\n' && byte != '\r') {
            if (used + 1 < sizeof(line)) { line[used++] = (char)byte; }
            continue;
        }
        line[used] = '\0';
        const bool stop = strcmp(line, STOP_LINE) == 0;
        used = 0;
        if (stop) { return true; }
    }
    return false;
}

static bool join_wifi(EventGroupHandle_t system, const char *device_id, char *ip, size_t cap)
{
    ESP_ERROR_CHECK(net_wifi_init(system, device_id));
    const esp_err_t err = net_wifi_apply();
    if (err != ESP_OK) {
        test_report_line("wifi down: %s, set wifi/ssid from the console of main", esp_err_to_name(err));
        return false;
    }
    for (int s = 0; s < CONFIG_BSS_LIVE_WIFI_WAIT_S; s++) {
        if ((xEventGroupWaitBits(system, APP_BIT_WIFI_OK, pdFALSE, pdTRUE, pdMS_TO_TICKS(LINK_WAIT_MS)) &
             APP_BIT_WIFI_OK) != 0) {
            esp_netif_ip_info_t info = {0};
            ESP_ERROR_CHECK(esp_netif_get_ip_info(esp_netif_get_handle_from_ifkey("WIFI_STA_DEF"), &info));
            snprintf(ip, cap, IPSTR, IP2STR(&info.ip));
            return true;
        }
    }
    test_report_line("wifi down: no link in %d s", CONFIG_BSS_LIVE_WIFI_WAIT_S);
    return false;
}

static void *take(size_t bytes)
{
    void *mem = heap_caps_malloc(bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (mem == NULL) {
        mem = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM);
        s_chain.in_psram = true;
    }
    return mem;
}

static esp_err_t build_spec(void)
{
    const size_t fft_bytes = dsp_spec_fft_workspace_bytes(GEN_GRID_FFT_SIZE);
    void *mem = take(fft_bytes);
    if (mem == NULL) { return ESP_ERR_NO_MEM; }
    esp_err_t err = dsp_spec_fft_init(&s_chain.fft, GEN_GRID_FFT_SIZE, mem, fft_bytes);
    for (size_t m = 0; err == ESP_OK && m < N_MICS; m++) {
        mem = take(dsp_spec_stft_workspace_bytes());
        err = mem == NULL ? ESP_ERR_NO_MEM
                          : dsp_spec_stft_init(&s_chain.analysis[m], s_chain.fft, mem,
                                               dsp_spec_stft_workspace_bytes());
    }
    for (size_t j = 0; err == ESP_OK && j < s_chain.job.seats; j++) {
        mem = take(dsp_spec_istft_workspace_bytes());
        err = mem == NULL ? ESP_ERR_NO_MEM
                          : dsp_spec_istft_init(&s_chain.synthesis[j], s_chain.fft, mem,
                                                dsp_spec_istft_workspace_bytes());
    }
    return err;
}

static uint32_t hops_of_ms(uint32_t ms)
{
    return (uint32_t)lrintf((float)ms * GEN_GRID_SAMPLE_RATE_HZ / ((float)MS_PER_S * GEN_GRID_HOP_SAMPLES));
}

// A delayed hop takes the angle held lookahead_s after it: the gate looks this far back (KEHOACH 3.8).
static uint32_t back_hops_of(const job_t *job)
{
    const uint32_t delay = hops_of_ms(job->delay_ms);
    const uint32_t ahead =
        (uint32_t)lrintf(GEN_AFE_BSS_LOOKAHEAD_S * GEN_GRID_SAMPLE_RATE_HZ / GEN_GRID_HOP_SAMPLES);
    return delay - (ahead < delay ? ahead : delay);
}

static dsp_afe_bss_config_t bss_config(const job_t *job)
{
    return (dsp_afe_bss_config_t){
        .mode = job->mode,
        .keep_rule = (dsp_afe_bss_keep_t)GEN_AFE_BSS_KEEP_RULE,
        .keep_deg = GEN_AFE_BSS_KEEP_DEG,
        .gate_slope = GEN_AFE_BSS_GATE_SLOPE,
        .conf_min = GEN_AFE_BSS_CONF_MIN,
        .seats_max = job->seats,
        .decision_s = GEN_AFE_BSS_DECISION_S,
        .recheck_s = GEN_AFE_BSS_RECHECK_S,
        .first_speech_s = GEN_AFE_BSS_FIRST_SPEECH_S,
        .confirm = GEN_AFE_BSS_CONFIRM,
        .occupancy_min = GEN_AFE_BSS_OCCUPANCY_MIN,
        .min_gap = GEN_AFE_BSS_MIN_GAP,
        .bins_per_gap = GEN_AFE_BSS_BINS_PER_GAP,
        .smooth_bins = GEN_AFE_BSS_SMOOTH_BINS,
        .prominence = GEN_AFE_BSS_PROMINENCE,
        .core_share = GEN_AFE_BSS_CORE_SHARE,
        .seat_follow = GEN_AFE_BSS_SEAT_FOLLOW,
        .boundary_share_min = GEN_AFE_BSS_BOUNDARY_SHARE_MIN,
        .boundary_follow = GEN_AFE_BSS_BOUNDARY_FOLLOW,
        .seat_forget_s = GEN_AFE_BSS_SEAT_FORGET_S,
        .update_every_hops = GEN_AFE_DOA_UPDATE_EVERY_HOPS,
        .spacing_m = GEN_ARRAY_SPACING_M,
        .speed_of_sound_m_s = GEN_ARRAY_SPEED_OF_SOUND_M_S,
        .history_s = (float)back_hops_of(job) * GEN_GRID_HOP_SAMPLES / GEN_GRID_SAMPLE_RATE_HZ,
    };
}

// The modules dsp_afe chains under spatial bss, with the product's settings (KEHOACH 3.2).
static esp_err_t build_chain(void)
{
    ESP_RETURN_ON_ERROR(build_spec(), TAG, "spec");
    const dsp_afe_hpf_config_t hpf = {.cutoff_hz = GEN_AFE_HPF_CUTOFF_HZ, .n_channels = N_MICS};
    void *mem = take(dsp_afe_hpf_workspace_bytes(&hpf));
    if (mem == NULL) { return ESP_ERR_NO_MEM; }
    ESP_RETURN_ON_ERROR(dsp_afe_hpf_init(&s_chain.hpf, &hpf, mem, dsp_afe_hpf_workspace_bytes(&hpf)), TAG,
                        "hpf");
    const dsp_afe_doa_config_t doa = {
        .spacing_m = GEN_ARRAY_SPACING_M,
        .speed_of_sound_m_s = GEN_ARRAY_SPEED_OF_SOUND_M_S,
        .band_min_hz = GEN_AFE_DOA_BAND_MIN_HZ,
        .band_max_hz = GEN_AFE_DOA_BAND_MAX_HZ > 0.0f ? GEN_AFE_DOA_BAND_MAX_HZ : GEN_ARRAY_ALIAS_HZ,
        .grid_step_deg = GEN_AFE_DOA_GRID_STEP_DEG,
        .smooth_tau_s = GEN_AFE_DOA_SMOOTH_TAU_S,
    };
    mem = take(dsp_afe_doa_workspace_bytes(&doa));
    if (mem == NULL) { return ESP_ERR_NO_MEM; }
    ESP_RETURN_ON_ERROR(dsp_afe_doa_init(&s_chain.doa, &doa, mem, dsp_afe_doa_workspace_bytes(&doa)), TAG,
                        "doa");
    const dsp_afe_vad_config_t vad = {.aggressiveness = vad_mode(), .hangover_ms = GEN_AFE_VAD_HANGOVER_MS};
    mem = take(dsp_afe_vad_workspace_bytes(&vad));
    if (mem == NULL) { return ESP_ERR_NO_MEM; }
    ESP_RETURN_ON_ERROR(dsp_afe_vad_init(&s_chain.vad_in, &vad, mem, dsp_afe_vad_workspace_bytes(&vad)), TAG,
                        "vad");
    const dsp_afe_bss_config_t bss = bss_config(&s_chain.job);
    mem = take(dsp_afe_bss_workspace_bytes(&bss));
    if (mem == NULL) { return ESP_ERR_NO_MEM; }
    ESP_RETURN_ON_ERROR(dsp_afe_bss_init(&s_chain.bss, &bss, mem, dsp_afe_bss_workspace_bytes(&bss)), TAG,
                        "bss");
    if (s_chain.job.ns == NS_OFF) { return ESP_OK; }
    s_chain.ns = dsp_afe_ns_omlsa_ops();
    s_chain.omlsa.floor_db = ns_floor_db();
    const size_t states = s_chain.job.ns == NS_EACH ? s_chain.job.seats : 1u;
    for (size_t j = 0; j < states; j++) {
        const size_t bytes = s_chain.ns->state_bytes(&s_chain.omlsa);
        s_chain.ns_state[j] = take(bytes);
        if (s_chain.ns_state[j] == NULL) { return ESP_ERR_NO_MEM; }
        ESP_RETURN_ON_ERROR(s_chain.ns->init(&s_chain.omlsa, s_chain.ns_state[j], bytes), TAG, "ns");
    }
    return ESP_OK;
}

static void apply_ns(void *state, dsp_spec_cplx_t *y)
{
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        s_chain.power[k] = y[k].re * y[k].re + y[k].im * y[k].im;
    }
    float speech_prob = 0.0f;
    if (s_chain.ns->process(&s_chain.omlsa, state, s_chain.power, NULL, s_chain.gain, &speech_prob) !=
        ESP_OK) {
        return;
    }
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        y[k].re *= s_chain.gain[k];
        y[k].im *= s_chain.gain[k];
    }
}

static void to_pcm(const float *hop, int16_t *pcm, size_t stride)
{
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        const float s = hop[i] * PCM_FULL_SCALE;
        pcm[i * stride] = s >= INT16_MAX ? INT16_MAX : s <= INT16_MIN ? INT16_MIN : (int16_t)lrintf(s);
    }
}

// The microphones of raw, stride channels a sample, then every seat of mean under its gate, into out.
static void emit(const int16_t *raw, size_t stride, const dsp_spec_cplx_t *mean, const float *gate)
{
    const uint8_t out_n = s_chain.out_channels;
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        for (size_t m = 0; m < N_MICS; m++) {
            s_chain.out[i * out_n + m] = raw[i * stride + m];
        }
    }
    for (size_t j = 0; j < s_chain.job.seats; j++) {
        for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
            s_chain.seat[k] = (dsp_spec_cplx_t){mean[k].re * gate[j], mean[k].im * gate[j]};
        }
        if (s_chain.job.ns == NS_EACH) { apply_ns(s_chain.ns_state[j], s_chain.seat); }
        dsp_spec_istft_synthesize(s_chain.synthesis[j], s_chain.seat, s_chain.synth);
        to_pcm(s_chain.synth, &s_chain.out[N_MICS + j], out_n);
    }
}

// Live, this hop; delayed, the hop delay_hops back under the seats held now. False while none is due.
static bool emit_due(const float *gate)
{
    if (s_chain.delay_hops == 0) {
        emit(s_chain.raw, s_chain.raw_channels, s_chain.mean, gate);
        return true;
    }
    const uint32_t slot = s_chain.hop % s_chain.delay_hops;
    const bool due = s_chain.hop >= s_chain.delay_hops;
    if (due) {
        float late[SEATS_MAX];
        uint8_t kept = 0;
        ESP_ERROR_CHECK(dsp_afe_bss_numbered_back(s_chain.bss, s_chain.back_hops, late, &kept));
        emit(s_chain.late_raw[slot], N_MICS, s_chain.late_mean[slot], late);
    }
    memcpy(s_chain.late_mean[slot], s_chain.mean, sizeof(s_chain.mean));
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        for (size_t m = 0; m < N_MICS; m++) {
            s_chain.late_raw[slot][i * N_MICS + m] = s_chain.raw[i * s_chain.raw_channels + m];
        }
    }
    return due;
}

// dsp_afe's order under bss: hpf, stft, balance, doa on the mics' raw vad, then the gates (KEHOACH 3.8).
static bool run_hop(float gate[SEATS_MAX], int16_t angle_deg[SEATS_MAX], uint8_t *seats)
{
    const uint8_t n = s_chain.raw_channels;
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        for (size_t m = 0; m < N_MICS; m++) {
            s_chain.pcm[m][i] = (float)s_chain.raw[i * n + m] / PCM_FULL_SCALE;
        }
    }
    for (uint8_t m = 0; m < N_MICS; m++) {
        dsp_afe_hpf_process(s_chain.hpf, m, s_chain.pcm[m], GEN_GRID_HOP_SAMPLES);
        dsp_spec_stft_analyze(s_chain.analysis[m], s_chain.pcm[m], s_chain.bins[m]);
    }
    if (s_chain.calib != NULL) { dsp_afe_balance_apply(s_chain.calib->balance, s_chain.bins[1]); }
    const bool update = s_chain.heard && s_chain.hop % GEN_AFE_DOA_UPDATE_EVERY_HOPS == 0;
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        s_chain.mics_mean[i] = 0.5f * (s_chain.pcm[0][i] + s_chain.pcm[1][i]);
    }
    bool speech = false;
    dsp_afe_vad_process(s_chain.vad_in, s_chain.mics_mean, &speech);
    dsp_afe_vad_detail_t detail;
    dsp_afe_vad_detail(s_chain.vad_in, &detail);
    s_chain.heard = detail.raw;
    dsp_afe_doa_result_t direction;
    dsp_afe_doa_process(s_chain.doa, s_chain.bins[0], s_chain.bins[1], update, &direction);
    dsp_afe_bss_process(s_chain.bss, s_chain.bins[0], s_chain.bins[1], direction.angle_deg,
                        direction.confidence, update, s_chain.kept, s_chain.rest);
    float kept_gate = 0.0f;
    dsp_afe_bss_state(s_chain.bss, seats, &kept_gate);
    dsp_afe_bss_numbered(s_chain.bss, gate, angle_deg);
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        s_chain.mean[k] = (dsp_spec_cplx_t){0.5f * (s_chain.bins[0][k].re + s_chain.bins[1][k].re),
                                            0.5f * (s_chain.bins[0][k].im + s_chain.bins[1][k].im)};
    }
    if (s_chain.job.ns == NS_MEAN) { apply_ns(s_chain.ns_state[0], s_chain.mean); }
    const bool due = emit_due(gate);
    s_chain.hop++;
    return due;
}

static void account(uint32_t cycles, const float *gate, const int16_t *angle_deg, uint8_t seats)
{
    taskENTER_CRITICAL(&s_lock);
    s_stats.hops++;
    s_stats.window_hops++;
    s_stats.window_cycles += cycles;
    s_stats.total_cycles += cycles;
    s_stats.window_peak = cycles > s_stats.window_peak ? cycles : s_stats.window_peak;
    s_stats.total_peak = cycles > s_stats.total_peak ? cycles : s_stats.total_peak;
    s_stats.seats = seats;
    memcpy(s_stats.gate, gate, sizeof(s_stats.gate));
    memcpy(s_stats.angle_deg, angle_deg, sizeof(s_stats.angle_deg));
    s_stats.heard_end = s_chain.hops_total - s_chain.delay_hops;
    s_stats.hops_total = s_chain.hops_total;
    taskEXIT_CRITICAL(&s_lock);
}

// The run ends once the ring has let out the last hop heard ahead of the stop.
static void stop_after_flush(void)
{
    s_chain.stopping = true;
    const uint32_t end = s_chain.hop + s_chain.delay_hops;
    if (end < s_chain.hops_total) { s_chain.hops_total = end; }
}

static void sach_task(void *arg)
{
    (void)arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    for (;;) {
        uint32_t seq = 0;
        const bool got = drv_audio_read_frame(s_chain.raw, &seq, DMA_WAIT_MS) == ESP_OK;
        if (got && atomic_load(&s_running)) {
            if (atomic_load(&s_stop_asked) && !s_chain.stopping) { stop_after_flush(); }
            float gate[SEATS_MAX];
            int16_t angle_deg[SEATS_MAX];
            uint8_t seats = 0;
            const uint32_t start = esp_cpu_get_cycle_count();
            const bool due = run_hop(gate, angle_deg, &seats);
            const uint32_t cycles = esp_cpu_get_cycle_count() - start;
            if (due) { svc_report_stream_push(seq, s_chain.out, NULL); }
            account(cycles, gate, angle_deg, seats);
            if (s_chain.hop >= s_chain.hops_total) {
                atomic_store(&s_running, false);
                xTaskNotifyGive(s_waiter);
            }
        }
        esp_task_wdt_reset();
    }
}

static void luong_task(void *arg)
{
    (void)arg;
    ESP_ERROR_CHECK(esp_task_wdt_add(NULL));
    for (;;) {
        svc_report_luong_step();
        esp_task_wdt_reset();
    }
}

static void print_status(void)
{
    taskENTER_CRITICAL(&s_lock);
    const uint32_t hops = s_stats.hops;
    const uint32_t window = s_stats.window_hops;
    const uint64_t cycles = s_stats.window_cycles;
    const uint32_t peak = s_stats.window_peak;
    const uint8_t seats = s_stats.seats;
    float gate[SEATS_MAX];
    int16_t angle_deg[SEATS_MAX];
    memcpy(gate, s_stats.gate, sizeof(gate));
    memcpy(angle_deg, s_stats.angle_deg, sizeof(angle_deg));
    const uint32_t heard_end = s_stats.heard_end;
    const uint32_t total = s_stats.hops_total;
    s_stats.window_hops = 0;
    s_stats.window_cycles = 0;
    s_stats.window_peak = 0;
    taskEXIT_CRITICAL(&s_lock);
    const float per_us = (float)esp_rom_get_cpu_ticks_per_us();
    const float mean_us = window > 0 ? (float)cycles / (float)window / per_us : 0.0f;
    const float hops_per_s = (float)GEN_GRID_SAMPLE_RATE_HZ / GEN_GRID_HOP_SAMPLES;
    const bool recording = hops < heard_end;
    const uint32_t ahead = recording ? heard_end - hops : (total > hops ? total - hops : 0);
    test_report_line(
        "t %.1f %s %u seats %u deg %d,%d,%d,%d gate %.2f,%.2f,%.2f,%.2f us %.0f/%.0f dropped %" PRIu32,
        (double)((float)hops / hops_per_s), recording ? "left" : "flush",
        (unsigned)ceilf((float)ahead / hops_per_s), (unsigned)seats, angle_deg[0], angle_deg[1], angle_deg[2],
        angle_deg[3], (double)gate[0], (double)gate[1], (double)gate[2], (double)gate[3], (double)mean_us,
        (double)((float)peak / per_us), svc_report_stream_dropped());
}

static void drain(void)
{
    for (int waited = 0; waited < DRAIN_WAIT_MS && !xStreamBufferIsEmpty(s_sb); waited += DRAIN_POLL_MS) {
        vTaskDelay(pdMS_TO_TICKS(DRAIN_POLL_MS));
    }
    svc_report_stream_stop();
}

static void summarise(void)
{
    drv_audio_stats_t audio;
    drv_audio_stats(&audio);
    const float per_us = (float)esp_rom_get_cpu_ticks_per_us();
    taskENTER_CRITICAL(&s_lock);
    const uint32_t hops = s_stats.hops;
    const uint64_t total = s_stats.total_cycles;
    const uint32_t peak = s_stats.total_peak;
    taskEXIT_CRITICAL(&s_lock);
    test_report_line("summary hops %" PRIu32 " us_mean %.1f us_peak %.1f dropped %" PRIu32
                     " dma_overflows %" PRIu32 " internal_free %u",
                     hops, (double)(hops > 0 ? (float)total / (float)hops / per_us : 0.0f),
                     (double)((float)peak / per_us), svc_report_stream_dropped(), audio.dma_overflows,
                     (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
}

static uint8_t stream_mode(uint8_t seats)
{
    return seats == 2   ? GEN_STREAM_MODE_RAW_SEATS2
           : seats == 3 ? GEN_STREAM_MODE_RAW_SEATS3
                        : GEN_STREAM_MODE_RAW_SEATS4;
}

static esp_err_t start_run(EventGroupHandle_t system)
{
    const job_t *job = &s_chain.job;
    s_chain.out_channels = (uint8_t)(N_MICS + job->seats);
    s_chain.delay_hops = hops_of_ms(job->delay_ms);
    s_chain.back_hops = back_hops_of(job);
    // The run hears delay_hops more so its last hop has an output too.
    s_chain.hops_total = hops_of_ms(job->seconds * MS_PER_S) + s_chain.delay_hops;
    s_chain.calib = load_calib();
    ESP_RETURN_ON_ERROR(build_chain(), TAG, "chain");
    const size_t ring_bytes = s_chain.delay_hops * (sizeof(*s_chain.late_mean) + sizeof(*s_chain.late_raw));
    if (s_chain.delay_hops > 0) {
        s_chain.late_mean =
            heap_caps_malloc(s_chain.delay_hops * sizeof(*s_chain.late_mean), MALLOC_CAP_SPIRAM);
        s_chain.late_raw =
            heap_caps_malloc(s_chain.delay_hops * sizeof(*s_chain.late_raw), MALLOC_CAP_SPIRAM);
        if (s_chain.late_mean == NULL || s_chain.late_raw == NULL) { return ESP_ERR_NO_MEM; }
    }
    s_sb = xStreamBufferCreateWithCaps(CONFIG_SVC_REPORT_STREAM_BUFFER_KB * 1024, 1, MALLOC_CAP_SPIRAM);
    if (s_sb == NULL) { return ESP_ERR_NO_MEM; }
    const svc_report_stream_config_t stream = {
        .sb = s_sb, .system = system, .raw_channels = s_chain.out_channels};
    ESP_RETURN_ON_ERROR(svc_report_stream_init(&stream), TAG, "stream");
    const uint32_t stream_s = job->seconds + STREAM_SLACK_S + (job->delay_ms + MS_PER_S - 1) / MS_PER_S;
    ESP_RETURN_ON_ERROR(svc_report_stream_start(stream_mode(job->seats), job->host, job->port, stream_s), TAG,
                        "stream start");
    test_report_line(
        "job mode %s seats %u ns %s seconds %" PRIu32 " delay %.1f s to %s:%u, balance %s, chain in "
        "%s, delay ring %u KB psram",
        MODE_NAMES[job->mode], (unsigned)job->seats, NS_NAMES[job->ns], job->seconds,
        (double)((float)job->delay_ms / MS_PER_S), job->host, (unsigned)job->port,
        s_chain.calib != NULL ? "calib/bal" : "none", s_chain.in_psram ? "psram, slower" : "internal ram",
        (unsigned)(ring_bytes / BYTES_PER_KB));
    xTaskCreatePinnedToCore(luong_task, "luong_task", LUONG_STACK_BYTES, NULL, LUONG_PRIORITY, NULL,
                            LUONG_CORE);
    s_waiter = xTaskGetCurrentTaskHandle();
    atomic_store(&s_running, true);
    return ESP_OK;
}

void app_main(void)
{
    ESP_ERROR_CHECK(bsp_board_init());
    ESP_ERROR_CHECK(sys_storage_init());
    const drv_audio_config_t audio = {
        .pcm_shift = pcm_shift(), .enable_tx = false, .dma_desc_num = DMA_DESC_NUM};
    ESP_ERROR_CHECK(drv_audio_init(&audio));
    s_chain.raw_channels = drv_audio_channels();
    // Drain DMA from boot so it never overflows; hops pass unprocessed until a job starts the run.
    xTaskCreatePinnedToCore(sach_task, "sach_task", SACH_STACK_BYTES, NULL, SACH_PRIORITY, NULL, SACH_CORE);
    if (!test_report_begin(REPORT_TAG, CONFIG_BSS_LIVE_SECONDS_MAX + REPORT_SPARE_LINES)) { abort(); }
    test_report_line("app esp-sr %s, grid 0x%08x, pcm_shift %u", esp_app_get_description()->version,
                     (unsigned)GEN_GRID_HASH, (unsigned)audio.pcm_shift);
    char device_id[DEVICE_ID_BYTES];
    char ip[IP_BYTES];
    ESP_ERROR_CHECK(sys_storage_device_id(device_id, sizeof(device_id)));
    EventGroupHandle_t system = xEventGroupCreate();
    if (join_wifi(system, device_id, ip, sizeof(ip))) {
        test_report_line("wifi %s", ip);
        test_report_line("ready modes %s,%s ns %s,%s,%s seats %d-%d seconds %d-%d delay 0-%d",
                         MODE_NAMES[DSP_AFE_BSS_LEARN], MODE_NAMES[DSP_AFE_BSS_FIXED], NS_NAMES[NS_OFF],
                         NS_NAMES[NS_MEAN], NS_NAMES[NS_EACH], SEATS_MIN, SEATS_MAX, SECONDS_MIN,
                         CONFIG_BSS_LIVE_SECONDS_MAX, CONFIG_BSS_LIVE_DELAY_MAX_S);
        read_job(&s_chain.job);
        const esp_err_t err = start_run(system);
        if (err == ESP_OK) {
            while (ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(STATUS_PERIOD_MS)) == 0) {
                print_status();
                if (!atomic_load(&s_stop_asked) && stop_typed()) {
                    atomic_store(&s_stop_asked, true);
                    test_report_line("stop asked: the delayed output flushes, then the stream closes");
                }
            }
            print_status();
            drain();
        } else {
            test_report_line("run refused: %s", esp_err_to_name(err));
        }
        release_console();
    }
    summarise();
    test_report_line("done");
    test_report_serve();
}
