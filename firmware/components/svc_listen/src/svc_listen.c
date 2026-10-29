#include "svc_listen.h"

#include <inttypes.h>
#include <stdatomic.h>
#include <stdio.h>
#include <string.h>

#include "ai_engine.h"
#include "app_err.h"
#include "dsp_spec/fft.h"
#include "dsp_spec/mel.h"
#include "dsp_spec/stft.h"
#include "dsp_spec/swift_dims.h"
#include "dsp_spec/swift_front.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "gen_grid.h"
#include "gen_listen.h"
#include "gen_speaker.h"
#include "storage_format.h"
#include "sys_storage.h"

#define FEATURES (GEN_LISTEN_N_BANDS + DSP_SPEC_SWIFT_DIMS)
#define FRONT_VALUES (DSP_SPEC_SWIFT_FRONT_SPECTRA * DSP_SPEC_SWIFT_FRONT_BANDS)
#define WINDOWS_MAX 4
// A queued window whose first hop the ring passes is dropped, never read torn.
#define RING_HOPS 512
#define LEAD_HOPS GEN_LISTEN_UTTERANCE_LEAD_HOPS
#define WINDOW_HOPS GEN_LISTEN_WINDOW_HOPS
#define PCM_FULL_SCALE 32768.0f // srpipe's to_float: int16 over 2^15, exact
#define PERMILLE_MAX 1000       // event.schema caps score and margin here
#define MEM_ALIGN 16
#define TAIL_HOPS ((GEN_SPEAKER_WINDOW_SAMPLES + GEN_GRID_HOP_SAMPLES - 1) / GEN_GRID_HOP_SAMPLES)
#define NO_SLOT (-1)
#define US_PER_S 1000000
#define MS_PER_S 1000

_Static_assert(AI_ENGINE_VARIANTS_MAX == LANG_VI_VARIANTS_MAX, "one command's readings fit one lexicon row");
_Static_assert(RING_HOPS >= 2 * WINDOW_HOPS, "the ring holds the window worked and the next one whole");
_Static_assert(RING_HOPS > TAIL_HOPS, "the PCM ring holds a speaker window");

typedef struct {
    uint32_t first, last; // hops, both in; last moves on while it is open
    uint32_t speech;      // the utterance's first vad hop
    uint32_t floor;       // no window reaches back past this hop
    bool open;            // its utterance still runs
    bool cut_back;        // outgrew WINDOW_HOPS: worked from its end once closed
    bool holed;           // seq broke while open: decided FRAME_GAP, never scored
    int64_t closed_us;
} window_t;

typedef struct {
    void *fft, *stft, *mel, *front, *dims;
} workspaces_t;

typedef struct {
    int16_t pcm[GEN_SPEAKER_WINDOW_SAMPLES];
    svc_listen_decision_t decision;
    uint64_t lead_energy, speech_energy; // int16 squared: its lead, its utterance
    uint32_t lead_samples, speech_samples;
} verify_slot_t;

typedef struct {
    bool on;                              // the roster's check, NVS speaker/chip
    int8_t enrolling;                     // slot whose next commands enrol, NO_SLOT without
    uint8_t enrolled;                     // of GEN_SPEAKER_ENROL_COMMANDS
    int64_t enrol_window_us;              // svc_listen_config_t's enrol_window_s
    int64_t enrol_until_us;               // when an unfinished enrolment ends
    uint8_t n_prints;                     // voiceprints of the loaded speaker entry, rows of prints
    uint8_t print_slot[APP_SPEAKERS_MAX]; // slot of each row, rising
    float means[APP_SPEAKERS_MAX];        // each row's over the cohort
    float stds[APP_SPEAKERS_MAX];
    uint32_t serial;
    size_t dims;
    storage_speaker_roster_t *roster; // as NVS speaker/roster keeps it
    float *unit, *prints, *enrol_units;
} speaker_t;

typedef struct {
    uint8_t (*units)[AI_ENGINE_VARIANTS_MAX][LANG_VI_UNITS_MAX];
    char (*ids)[APP_EVT_ID_MAX_BYTES];
    ai_engine_lexicon_t *lexicon;
    uint32_t version;
} table_t;

static const char *TAG = "svc_listen";

static struct {
    bool ready;
    dsp_spec_stft_t *stft;
    dsp_spec_mel_t *mel;
    dsp_spec_swift_front_t *front; // NULL without a pitch in the image: the pitch dims stay 0
    dsp_spec_swift_dims_t *dims;
    float *batch;          // a call's front frames, FRONT_VALUES each
    float *log_f0, *sure;  // a call's decoded frames
    uint16_t *peaks;       // each hop's |PCM| peak, at its ring slot
    size_t step, filled;   // front frames a network call takes; those in batch
    uint32_t right;        // frames swift_dims waits for
    uint32_t stream_first; // the hop the pitch stream restarted at
    uint32_t pitch_seq;    // the newest hop whose pitch dims are in the ring
    float *hop;
    dsp_spec_cplx_t *bins;
    float (*log_mel)[GEN_LISTEN_N_BANDS];
    float (*pitch_ring)[DSP_SPEC_SWIFT_DIMS];
    table_t tables[2]; // the one in use, and a spare to read a new set into
    size_t active;
    lang_vi_dialect_t dialects;
    uint16_t reject, margin, syllable;
    bool wake;         // a wake in the image: windows only in LENH
    bool commanding;   // LENH
    bool woke_pending; // a WAKE event svc_listen_work has not handed out
    uint16_t wake_th, wake_score;
    uint32_t woke;          // the hop whose score crossed wake_th
    uint32_t command_until; // LENH ends at this hop with no utterance running
    uint32_t wait_hops;     // SVC_LISTEN_COMMAND_WAIT_MS on the grid
    bool started, in_run, has_open, working;
    uint32_t next_seq, after, run_first, run_last, at;
    window_t queue[WINDOWS_MAX];
    size_t head, count;
    int64_t work_us;
    int16_t (*pcm_ring)[GEN_GRID_HOP_SAMPLES]; // only with speaker in the image
    verify_slot_t *slots;                      // SVC_LISTEN_VERIFY_SLOTS
    speaker_t spk;                             // xac_task's alone after init
} s;

// Checking on or an enrolment running: xac_task writes it, nhan_task reads it (KEHOACH 5.3).
static atomic_bool s_diverting;

static void *take(size_t bytes)
{
    return heap_caps_aligned_alloc(MEM_ALIGN, bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
}

static bool take_table(table_t *t)
{
    t->units = take(AI_ENGINE_COMMANDS_MAX * sizeof(*t->units));
    t->ids = take(AI_ENGINE_COMMANDS_MAX * sizeof(*t->ids));
    t->lexicon = take(sizeof(*t->lexicon));
    return t->units != NULL && t->ids != NULL && t->lexicon != NULL;
}

static bool take_speaker(void)
{
    s.spk.dims = ai_engine_speaker_dims();
    s.pcm_ring = take(RING_HOPS * sizeof(*s.pcm_ring));
    s.slots = take(SVC_LISTEN_VERIFY_SLOTS * sizeof(*s.slots));
    s.spk.roster = take(sizeof(*s.spk.roster));
    s.spk.unit = take(s.spk.dims * sizeof(float));
    s.spk.prints = take(APP_SPEAKERS_MAX * s.spk.dims * sizeof(float));
    s.spk.enrol_units = take(GEN_SPEAKER_ENROL_COMMANDS * s.spk.dims * sizeof(float));
    return s.pcm_ring != NULL && s.slots != NULL && s.spk.roster != NULL && s.spk.unit != NULL &&
           s.spk.prints != NULL && s.spk.enrol_units != NULL;
}

static void tpl_key(size_t slot, char key[STORAGE_NVS_NAME_MAX_BYTES])
{
    snprintf(key, STORAGE_NVS_NAME_MAX_BYTES, STORAGE_KEY_SPK_TPL_PREFIX "%u", (unsigned)slot);
}

static void divert(void)
{
    atomic_store(&s_diverting, s.spk.on || s.spk.enrolling != NO_SLOT);
}

// A voiceprint enrolled on another speaker entry lies in another space, so it stays unused (KEHOACH 3.17).
static void load_prints(uint32_t model)
{
    speaker_t *k = &s.spk;
    for (size_t slot = 0; slot < APP_SPEAKERS_MAX; slot++) {
        char key[STORAGE_NVS_NAME_MAX_BYTES];
        tpl_key(slot, key);
        const storage_speaker_slot_t *who = &k->roster->slot[slot];
        if (who->id[0] == '\0' || who->model_id != model || model == 0) { continue; }
        float *row = k->prints + k->n_prints * k->dims;
        if (sys_storage_get_blob(STORAGE_NS_SPEAKER, key, row, k->dims * sizeof(float)) == ESP_OK &&
            ai_engine_speaker_norm(row, &k->means[k->n_prints], &k->stds[k->n_prints]) == ESP_OK) {
            k->print_slot[k->n_prints++] = (uint8_t)slot;
        }
    }
}

// Only a roster read whole, or surely absent, lets free slots lose their voiceprints (KEHOACH 6.2).
static void load_speaker(void)
{
    speaker_t *k = &s.spk;
    uint8_t on = 0;
    k->on = sys_storage_get_u8(STORAGE_NS_SPEAKER, STORAGE_KEY_SPK_CHIP, &on) == ESP_OK && on != 0;
    k->enrolling = NO_SLOT;
    const esp_err_t read =
        sys_storage_get_blob(STORAGE_NS_SPEAKER, STORAGE_KEY_SPK_ROSTER, k->roster, sizeof(*k->roster));
    const bool sure = read == ESP_OK || read == ESP_ERR_NOT_FOUND || read == ESP_ERR_INVALID_SIZE;
    if (read != ESP_OK || k->roster->format != STORAGE_SPEAKER_ROSTER_FORMAT) {
        memset(k->roster, 0, sizeof(*k->roster));
        k->roster->format = STORAGE_SPEAKER_ROSTER_FORMAT;
    }
    for (size_t slot = 0; sure && slot < APP_SPEAKERS_MAX; slot++) {
        char key[STORAGE_NVS_NAME_MAX_BYTES];
        tpl_key(slot, key);
        if (k->roster->slot[slot].id[0] == '\0') { (void)sys_storage_erase(STORAGE_NS_SPEAKER, key); }
    }
    (void)sys_storage_erase(STORAGE_NS_SPEAKER, STORAGE_KEY_SPK_TPL);
    (void)sys_storage_erase(STORAGE_NS_SPEAKER, STORAGE_KEY_SPK_TPL_MODEL);
    load_prints(ai_engine_speaker_model_id());
    divert();
    ESP_LOGI(TAG, "speaker check %s, roster v%" PRIu32 " (%s), %u voiceprints of this model",
             k->on ? "on" : "off", k->roster->version, esp_err_to_name(read), (unsigned)k->n_prints);
}

static void release(workspaces_t *w)
{
    void *const blocks[] = {s.pcm_ring,
                            s.slots,
                            s.spk.roster,
                            s.spk.unit,
                            s.spk.prints,
                            s.spk.enrol_units,
                            s.hop,
                            s.bins,
                            s.log_mel,
                            s.pitch_ring,
                            s.batch,
                            s.log_f0,
                            s.peaks,
                            s.tables[0].units,
                            s.tables[0].ids,
                            s.tables[0].lexicon,
                            s.tables[1].units,
                            s.tables[1].ids,
                            s.tables[1].lexicon,
                            w->fft,
                            w->stft,
                            w->mel,
                            w->front,
                            w->dims};
    for (size_t i = 0; i < sizeof(blocks) / sizeof(blocks[0]); i++) {
        heap_caps_free(blocks[i]);
    }
    memset(&s, 0, sizeof(s));
    atomic_store(&s_diverting, false);
}

// SwiftF0's front end and dims over the tables and the call size of the image's pitch (KEHOACH 3.11
// step 3.4).
static esp_err_t pitch_front(workspaces_t *w)
{
    if (!ai_engine_has(AI_ENGINE_MODEL_PITCH)) {
        ESP_LOGW(TAG, "no pitch in the image: the pitch dims stay 0");
        return ESP_OK;
    }
    const dsp_spec_swift_dims_config_t dims_cfg = GEN_LISTEN_SWIFT_DIMS_CONFIG;
    const size_t front_bytes = dsp_spec_swift_front_workspace_bytes();
    const size_t dims_bytes = dsp_spec_swift_dims_workspace_bytes(&dims_cfg);
    s.step = ai_engine_pitch_frames();
    s.right = (uint32_t)dims_cfg.right_frames;
    w->front = take(front_bytes);
    w->dims = take(dims_bytes);
    s.batch = take(s.step * FRONT_VALUES * sizeof(float));
    s.log_f0 = take(2 * s.step * sizeof(float));
    s.peaks = take(RING_HOPS * sizeof(*s.peaks));
    if (w->front == NULL || w->dims == NULL || s.batch == NULL || s.log_f0 == NULL || s.peaks == NULL) {
        return ESP_ERR_NO_MEM;
    }
    s.sure = s.log_f0 + s.step;
    dsp_spec_swift_front_tables_t tables[DSP_SPEC_SWIFT_FRONT_SPECTRA];
    esp_err_t err = ai_engine_pitch_tables(tables);
    if (err == ESP_OK) { err = dsp_spec_swift_front_init(&s.front, tables, w->front, front_bytes); }
    if (err == ESP_OK) { err = dsp_spec_swift_dims_init(&s.dims, &dims_cfg, w->dims, dims_bytes); }
    return err;
}

static esp_err_t front_end(workspaces_t *w)
{
    const dsp_spec_mel_config_t mel_cfg = GEN_LISTEN_MEL_CONFIG;
    const size_t fft_bytes = dsp_spec_fft_workspace_bytes(GEN_GRID_FFT_SIZE);
    const size_t stft_bytes = dsp_spec_stft_workspace_bytes();
    const size_t mel_bytes = dsp_spec_mel_workspace_bytes(&mel_cfg);
    w->fft = take(fft_bytes);
    w->stft = take(stft_bytes);
    w->mel = take(mel_bytes);
    if (w->fft == NULL || w->stft == NULL || w->mel == NULL) { return ESP_ERR_NO_MEM; }
    dsp_spec_fft_t *fft = NULL;
    esp_err_t err = dsp_spec_fft_init(&fft, GEN_GRID_FFT_SIZE, w->fft, fft_bytes);
    if (err == ESP_OK) { err = dsp_spec_stft_init(&s.stft, fft, w->stft, stft_bytes); }
    if (err == ESP_OK) { err = dsp_spec_mel_init(&s.mel, &mel_cfg, w->mel, mel_bytes); }
    return err == ESP_OK ? pitch_front(w) : err;
}

static bool well_formed(const svc_listen_commands_t *c)
{
    return c != NULL && c->texts != NULL && c->ids != NULL && c->n_commands > 0 &&
           c->n_commands <= AI_ENGINE_COMMANDS_MAX;
}

static esp_err_t build_lexicon(const svc_listen_commands_t *c, table_t *t, uint16_t *unreadable)
{
    size_t variants = 0;
    memset(t->lexicon, 0, sizeof(*t->lexicon));
    for (uint16_t k = 0; k < c->n_commands; k++) {
        lang_vi_pron_t pron;
        const esp_err_t err = lang_vi_lexicon_entry(c->texts[k], s.dialects, &pron);
        if (err != ESP_OK) {
            ESP_LOGE(TAG, "command %s: lang_vi cannot read \"%s\" (%s)", c->ids[k], c->texts[k],
                     esp_err_to_name(err));
            *unreadable = k;
            return APP_ERR_COMMANDS_INVALID;
        }
        t->lexicon->n_variants[k] = pron.n_variants;
        for (uint8_t v = 0; v < pron.n_variants; v++) {
            memcpy(t->units[k][v], pron.units[v], pron.n_units[v]);
            t->lexicon->variants[k][v] =
                (ai_engine_seq_t){.n_units = pron.n_units[v], .units = t->units[k][v]};
        }
        strlcpy(t->ids[k], c->ids[k], APP_EVT_ID_MAX_BYTES);
        variants += pron.n_variants;
    }
    t->lexicon->n_commands = c->n_commands;
    t->version = c->version;
    ESP_LOGI(TAG, "commands v%" PRIu32 ": %u commands, %u readings", c->version, (unsigned)c->n_commands,
             (unsigned)variants);
    return ESP_OK;
}

esp_err_t svc_listen_init(const svc_listen_config_t *cfg)
{
    if (s.ready) { return ESP_ERR_INVALID_STATE; }
    if (cfg == NULL || !well_formed(&cfg->commands) || cfg->enrol_window_s == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!ai_engine_has(AI_ENGINE_MODEL_COMMAND)) { return ESP_ERR_NOT_SUPPORTED; }
    // A model of another band count reads every frame wrong (ADR-0017).
    const size_t takes = ai_engine_command_features();
    if (takes != 0 && takes != FEATURES) {
        ESP_LOGE(TAG, "the command model takes %u features a hop, listen.yaml gives %u", (unsigned)takes,
                 (unsigned)FEATURES);
        return ESP_ERR_INVALID_SIZE;
    }
    workspaces_t w = {0};
    s.hop = take(GEN_GRID_HOP_SAMPLES * sizeof(float));
    s.bins = take(GEN_GRID_N_BINS * sizeof(dsp_spec_cplx_t));
    s.log_mel = take(RING_HOPS * sizeof(*s.log_mel));
    s.pitch_ring = take(RING_HOPS * sizeof(*s.pitch_ring));
    const bool tables = take_table(&s.tables[0]) && take_table(&s.tables[1]);
    const bool rings = s.log_mel != NULL && s.pitch_ring != NULL;
    const bool speaker = !ai_engine_has(AI_ENGINE_MODEL_SPEAKER) || take_speaker();
    esp_err_t err =
        s.hop != NULL && s.bins != NULL && rings && tables && speaker ? front_end(&w) : ESP_ERR_NO_MEM;
    uint16_t unreadable = 0;
    s.dialects = cfg->dialects;
    if (err == ESP_OK) { err = build_lexicon(&cfg->commands, &s.tables[0], &unreadable); }
    if (err == ESP_OK) { err = ai_engine_command_prepare(s.tables[0].lexicon); }
    if (err != ESP_OK) {
        release(&w);
        return err;
    }
    if (s.pcm_ring != NULL) { load_speaker(); }
    s.active = 0;
    s.reject = cfg->reject_permille;
    s.margin = cfg->margin_permille;
    s.syllable = cfg->syllable_permille;
    s.wake = ai_engine_has(AI_ENGINE_MODEL_WAKE);
    s.wake_th = cfg->wake_permille;
    s.wait_hops = (uint32_t)((uint64_t)CONFIG_SVC_LISTEN_COMMAND_WAIT_MS * GEN_GRID_SAMPLE_RATE_HZ /
                             ((uint64_t)MS_PER_S * GEN_GRID_HOP_SAMPLES));
    s.spk.enrol_window_us = (int64_t)cfg->enrol_window_s * US_PER_S;
    s.ready = true;
    return ESP_OK;
}

esp_err_t svc_listen_set_commands(const svc_listen_commands_t *commands, uint16_t *unreadable)
{
    if (!s.ready || svc_listen_busy()) { return ESP_ERR_INVALID_STATE; }
    if (!well_formed(commands) || unreadable == NULL) { return ESP_ERR_INVALID_ARG; }
    const size_t spare = 1 - s.active;
    esp_err_t err = build_lexicon(commands, &s.tables[spare], unreadable);
    if (err == ESP_OK) { err = ai_engine_command_prepare(s.tables[spare].lexicon); }
    if (err == ESP_OK) {
        s.active = spare;
        return ESP_OK;
    }
    // A prepare that failed half way leaves ai_engine on no set; the one still in use streams again.
    ai_engine_command_prepare(s.tables[s.active].lexicon);
    return err;
}

uint32_t svc_listen_commands_version(void)
{
    return s.ready ? s.tables[s.active].version : 0;
}

static void to_float(const int16_t *pcm, float *out)
{
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        out[i] = (float)pcm[i] / PCM_FULL_SCALE;
    }
}

static window_t *open_window(void)
{
    return s.has_open ? &s.queue[(s.head + s.count - 1) % WINDOWS_MAX] : NULL;
}

// A window left open in ai_engine refuses every new set until a later one is scored (KEHOACH 4.5.5).
static void stop_engine(void)
{
    if (s.working) { ai_engine_command_abort(); }
    s.working = false;
}

static void stop_working(const window_t *w)
{
    if (s.count > 0 && w == &s.queue[s.head]) { stop_engine(); }
}

static void drop_open(void)
{
    window_t *w = open_window();
    if (w == NULL) { return; }
    stop_working(w);
    s.count--;
    s.has_open = false;
}

// A holed window keeps its place in the queue, so its FRAME_GAP leaves in order among the decisions.
static void hole_open(void)
{
    window_t *w = open_window();
    if (w == NULL) { return; }
    stop_working(w);
    w->open = false;
    w->holed = true;
    w->closed_us = esp_timer_get_time();
    s.has_open = false;
}

static esp_err_t open_utterance(uint32_t seq)
{
    s.run_first = seq;
    if (s.count == WINDOWS_MAX) { return ESP_ERR_NO_MEM; }
    const uint32_t lead = seq >= LEAD_HOPS ? seq - LEAD_HOPS : 0;
    s.queue[(s.head + s.count) % WINDOWS_MAX] = (window_t){
        .first = lead > s.after ? lead : s.after, .last = seq, .speech = seq, .floor = s.after, .open = true};
    s.count++;
    s.has_open = true;
    return ESP_OK;
}

static void close_utterance(void)
{
    s.in_run = false;
    const bool kept = s.run_last - s.run_first >= GEN_LISTEN_UTTERANCE_MIN_HOPS;
    const uint32_t end = s.run_last + 1;
    window_t *w = open_window();
    if (w != NULL && !kept) { drop_open(); }
    if (w != NULL && kept) {
        w->open = false;
        w->last = end;
        w->closed_us = esp_timer_get_time();
        if (w->cut_back) { w->first = end + 1 - WINDOW_HOPS > w->floor ? end + 1 - WINDOW_HOPS : w->floor; }
        s.has_open = false;
    }
    if (kept) { s.after = end + 1; }
}

// The window's end only moves later, so every hop up to the one after the run's last is in it (KEHOACH 5.4).
static void extend_open(uint32_t seq)
{
    window_t *w = open_window();
    if (w == NULL) { return; }
    w->last = s.run_last + 1 <= seq ? s.run_last + 1 : seq;
    if (!w->cut_back && w->last + 1 - w->first > WINDOW_HOPS) {
        w->cut_back = true;
        stop_working(w);
    }
}

static void pitch_restart(uint32_t seq)
{
    s.stream_first = seq;
    s.pitch_seq = seq - 1;
    s.filled = 0;
    if (s.front == NULL) { return; }
    dsp_spec_swift_front_reset(s.front);
    dsp_spec_swift_dims_reset(s.dims);
    ai_engine_pitch_reset();
}

static bool pitched(uint32_t hop)
{
    return (int32_t)(s.pitch_seq - hop) >= 0;
}

// A closed window short of its pitch columns at a break never gets them: it goes out FRAME_GAP (KEHOACH 5.4).
static void hole_unpitched(void)
{
    for (size_t k = 0; k < s.count; k++) {
        window_t *w = &s.queue[(s.head + k) % WINDOWS_MAX];
        if (w->holed || pitched(w->last)) { continue; }
        stop_working(w);
        w->holed = true;
    }
}

// A call's decoded frames, gated by their hops' peaks as swift-f0 gates them, through swift_dims; each
// frame's dims land at the slot of the hop right frames back. Its last front frame is seq's, DELAY_HOPS back.
static esp_err_t pitch_call(uint32_t seq)
{
    esp_err_t err = ai_engine_pitch_run(s.batch, s.log_f0, s.sure);
    const uint32_t first =
        seq - DSP_SPEC_SWIFT_FRONT_DELAY_HOPS - (uint32_t)(s.step - 1) - (uint32_t)ai_engine_pitch_delay();
    for (size_t j = 0; err == ESP_OK && j < s.step; j++) {
        const uint32_t hop = first + (uint32_t)j;
        if ((int32_t)(hop - s.stream_first) < 0) { continue; }
        const bool silent = (float)s.peaks[hop % RING_HOPS] / PCM_FULL_SCALE < GEN_LISTEN_PITCH_SILENCE_PEAK;
        float dims[DSP_SPEC_SWIFT_DIMS];
        bool given = false;
        err = dsp_spec_swift_dims_push(s.dims, s.log_f0[j], silent ? 0.0f : s.sure[j], dims, &given);
        if (err != ESP_OK || !given) { continue; }
        s.pitch_seq = hop - s.right;
        memcpy(s.pitch_ring[s.pitch_seq % RING_HOPS], dims, sizeof(dims));
    }
    return err;
}

// SwiftF0 a hop at a time; without a pitch in the image the hop's pitch dims are 0 at once (KEHOACH 3.11).
static esp_err_t pitch_hop(const int16_t *pcm, uint32_t seq)
{
    if (s.front == NULL) {
        memset(s.pitch_ring[seq % RING_HOPS], 0, sizeof(s.pitch_ring[0]));
        s.pitch_seq = seq;
        return ESP_OK;
    }
    uint16_t peak = 0;
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        const uint16_t magnitude = (uint16_t)(pcm[i] < 0 ? -(int32_t)pcm[i] : pcm[i]);
        peak = magnitude > peak ? magnitude : peak;
    }
    s.peaks[seq % RING_HOPS] = peak;
    bool ready = false;
    const esp_err_t err = dsp_spec_swift_front_push(s.front, pcm, s.batch + s.filled * FRONT_VALUES, &ready);
    if (err != ESP_OK || !ready || ++s.filled < s.step) { return err; }
    s.filled = 0;
    return pitch_call(seq);
}

// Without a wake every utterance gets a window; with one, only those of LENH (KEHOACH 5.4).
static bool commands_open(void)
{
    return !s.wake || s.commanding;
}

static void leave_command(void)
{
    s.commanding = false;
    ai_engine_wake_reset();
}

// The score ai_engine_wake_step returns is the frame's ai_engine_wake_delay hops back (KEHOACH 3.11).
static esp_err_t hear_wake(uint32_t seq)
{
    uint16_t score = 0;
    const esp_err_t err = ai_engine_wake_step(s.log_mel[seq % RING_HOPS], &score);
    if (err != ESP_OK || score < s.wake_th) { return err; }
    s.commanding = true;
    s.woke = seq - (uint32_t)ai_engine_wake_delay();
    s.wake_score = score;
    s.woke_pending = true;
    s.command_until = seq + s.wait_hops;
    if ((int32_t)(s.woke + 1 - s.after) > 0) { s.after = s.woke + 1; }
    if (!s.in_run) { return ESP_OK; }
    // The utterance running through the wake word opens at the hop after it (KEHOACH 5.4).
    const esp_err_t opened = open_utterance(seq);
    window_t *w = open_window();
    if (w != NULL) { w->speech = s.after; }
    s.run_first = s.after;
    return opened;
}

static void wait_command(uint32_t seq)
{
    if (!s.commanding) { return; }
    if (s.in_run || s.count > 0) {
        s.command_until = seq + s.wait_hops;
    } else if ((int32_t)(seq - s.command_until) >= 0) {
        leave_command();
    }
}

esp_err_t svc_listen_feed(const int16_t *pcm, uint32_t seq, bool vad, bool gap)
{
    if (!s.ready) { return ESP_ERR_INVALID_STATE; }
    if (pcm == NULL) { return ESP_ERR_INVALID_ARG; }
    if (!s.started || seq != s.next_seq || gap) {
        dsp_spec_stft_reset(s.stft);
        hole_open();
        hole_unpitched();
        pitch_restart(seq);
        if (s.wake) { ai_engine_wake_reset(); }
        s.in_run = false;
        s.after = seq;
    }
    s.started = true;
    s.next_seq = seq + 1;
    const size_t slot = seq % RING_HOPS;
    if (s.pcm_ring != NULL) { memcpy(s.pcm_ring[slot], pcm, sizeof(s.pcm_ring[slot])); }
    to_float(pcm, s.hop);
    esp_err_t err = dsp_spec_stft_analyze(s.stft, s.hop, s.bins);
    if (err == ESP_OK) { err = dsp_spec_mel_log(s.mel, s.bins, s.log_mel[slot]); }
    if (err == ESP_OK) { err = pitch_hop(pcm, seq); }
    if (err == ESP_OK && s.wake && !s.commanding) { err = hear_wake(seq); }
    if (err != ESP_OK) { return err; }
    if (s.in_run && seq - s.run_last > GEN_LISTEN_UTTERANCE_GAP_HOPS) { close_utterance(); }
    if (vad) {
        if (!s.in_run) { s.run_first = seq; }
        if (!s.in_run && commands_open()) { err = open_utterance(seq); }
        s.in_run = true;
        s.run_last = seq;
    }
    extend_open(seq);
    wait_command(seq);
    return err;
}

// From the first vad hop, even of a click the close then drops: catching up is the costly part (KEHOACH 5.4).
static bool workable(const window_t *w)
{
    return !w->open || !w->cut_back;
}

bool svc_listen_pending(void)
{
    if (s.ready && s.woke_pending) { return true; }
    if (!s.ready || s.count == 0) { return false; }
    const window_t *w = &s.queue[s.head];
    const uint32_t next = s.working ? s.at : w->first;
    // A hop is worked once its pitch dims are in, 35 frames or more after its log-mel (KEHOACH 3.11
    // step 3.4).
    const bool due = w->holed || (s.working && s.at > w->last) || pitched(next);
    return workable(w) && (!w->open || !s.working || s.at <= w->last) && due;
}

bool svc_listen_busy(void)
{
    return s.ready && (s.count > 0 || s.in_run);
}

// A window decided in LENH sends the listener back to NGHE (KEHOACH 5.4).
static void done(void)
{
    s.working = false;
    s.head = (s.head + 1) % WINDOWS_MAX;
    s.count--;
    if (s.commanding) { leave_command(); }
}

static void drop(const window_t *w, const char *why)
{
    ESP_LOGW(TAG, "window %u..%u dropped: %s", (unsigned)w->first, (unsigned)w->last, why);
    if (w->open) { s.has_open = false; }
    stop_engine();
    done();
}

static void decide(const window_t *w, const ai_engine_command_result_t *r, svc_listen_decision_t *out)
{
    memset(out, 0, sizeof(*out));
    app_event_t *e = &out->event;
    e->seq = w->last;
    e->doa_deg = -1;
    e->score_permille = r->score_permille < PERMILLE_MAX ? r->score_permille : PERMILLE_MAX;
    e->margin_permille = r->margin_permille < PERMILLE_MAX ? r->margin_permille : PERMILLE_MAX;
    if (r->command >= 0) {
        e->kind = APP_EVT_COMMAND;
        strlcpy(e->command_id, s.tables[s.active].ids[r->command], sizeof(e->command_id));
    } else {
        e->kind = APP_EVT_REJECT;
        // KEHOACH 5.4's order: far from the free loop, too close to the second, a syllable too far, a part.
        const char *code = r->free_gap_permille > s.reject         ? APP_CODE_LOW_SCORE
                           : r->margin_permille < s.margin         ? APP_CODE_LOW_MARGIN
                           : r->syllable_gap_permille > s.syllable ? APP_CODE_LOW_SYLLABLE
                                                                   : APP_CODE_PART;
        strlcpy(e->code, code, sizeof(e->code));
    }
    out->first_seq = w->first;
    out->speech_seq = w->speech;
    out->free_gap_permille = r->free_gap_permille;
    out->syllable_gap_permille = r->syllable_gap_permille;
    out->work_us = (uint32_t)s.work_us;
    out->close_us = (uint32_t)(esp_timer_get_time() - w->closed_us);
}

static void decide_wake(svc_listen_decision_t *out)
{
    memset(out, 0, sizeof(*out));
    out->event.kind = APP_EVT_WAKE;
    out->event.seq = s.woke;
    out->event.doa_deg = -1;
    out->event.score_permille = s.wake_score;
    out->first_seq = s.woke;
    s.woke_pending = false;
}

static void decide_holed(const window_t *w, svc_listen_decision_t *out)
{
    memset(out, 0, sizeof(*out));
    out->event.kind = APP_EVT_REJECT;
    out->event.seq = w->last;
    out->event.doa_deg = -1;
    strlcpy(out->event.code, APP_CODE_FRAME_GAP, sizeof(out->event.code));
    out->first_seq = w->first;
    out->close_us = (uint32_t)(esp_timer_get_time() - w->closed_us);
}

bool svc_listen_work(svc_listen_decision_t *out)
{
    if (!svc_listen_pending() || out == NULL) { return false; }
    if (s.woke_pending) {
        decide_wake(out);
        return true;
    }
    const window_t *w = &s.queue[s.head];
    if (w->holed) {
        decide_holed(w, out);
        done();
        return true;
    }
    const int64_t from_us = esp_timer_get_time();
    if (s.next_seq - (s.working ? s.at : w->first) > RING_HOPS) {
        drop(w, "the ring moved past it");
        return false;
    }
    if (!s.working) {
        if (ai_engine_command_begin() != ESP_OK) {
            drop(w, "no command window");
            return false;
        }
        s.working = true;
        s.at = w->first;
        s.work_us = 0;
    }
    if (s.at <= w->last) {
        if (!pitched(s.at)) { return false; }
        const size_t slot = s.at % RING_HOPS;
        float features[FEATURES];
        memcpy(features, s.log_mel[slot], sizeof(s.log_mel[slot]));
        memcpy(features + GEN_LISTEN_N_BANDS, s.pitch_ring[slot], sizeof(s.pitch_ring[slot]));
        const esp_err_t err = ai_engine_command_step(features);
        if (err != ESP_OK) {
            drop(w, esp_err_to_name(err));
            return false;
        }
        s.at++;
        s.work_us += esp_timer_get_time() - from_us;
        return false;
    }
    ai_engine_command_result_t result;
    const esp_err_t err = ai_engine_command_score(s.tables[s.active].lexicon, &result);
    s.work_us += esp_timer_get_time() - from_us;
    if (err != ESP_OK) {
        drop(w, esp_err_to_name(err));
        return false;
    }
    decide(w, &result, out);
    done();
    return true;
}

bool svc_listen_verifies(void)
{
    return s.ready && s.pcm_ring != NULL && atomic_load(&s_diverting);
}

static uint64_t energy_of(uint32_t from, uint32_t to)
{
    uint64_t total = 0;
    for (uint32_t h = from; h != to; h++) {
        const int16_t *x = s.pcm_ring[h % RING_HOPS];
        for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
            total += (uint64_t)((int32_t)x[i] * x[i]);
        }
    }
    return total;
}

// A lead the ring has passed leaves the sums empty: the noise bin is then the quietest (ADR-0024).
static void weigh_noise(const svc_listen_decision_t *decision, verify_slot_t *v)
{
    const uint32_t first = decision->first_seq;
    const uint32_t last = decision->event.seq;
    const uint32_t speech = decision->speech_seq > first ? decision->speech_seq : first;
    v->lead_energy = v->speech_energy = 0;
    v->lead_samples = v->speech_samples = 0;
    if (s.next_seq - first > RING_HOPS || speech > last) { return; }
    v->lead_energy = energy_of(first, speech);
    v->lead_samples = (speech - first) * GEN_GRID_HOP_SAMPLES;
    v->speech_energy = energy_of(speech, last + 1);
    v->speech_samples = (last + 1 - speech) * GEN_GRID_HOP_SAMPLES;
}

// The window's last TAIL_HOPS hops, less the oldest samples past a speaker window, zeros ahead of a shorter
// one.
esp_err_t svc_listen_hold(const svc_listen_decision_t *decision, uint8_t slot)
{
    if (!s.ready || s.pcm_ring == NULL || decision == NULL || slot >= SVC_LISTEN_VERIFY_SLOTS) {
        return ESP_ERR_INVALID_ARG;
    }
    const uint32_t last = decision->event.seq;
    const uint32_t hops = last + 1 - decision->first_seq;
    const uint32_t from = hops > TAIL_HOPS ? last + 1 - TAIL_HOPS : decision->first_seq;
    if (s.next_seq - from > RING_HOPS) { return ESP_ERR_NOT_FOUND; }
    verify_slot_t *v = &s.slots[slot];
    const size_t have = (size_t)(last + 1 - from) * GEN_GRID_HOP_SAMPLES;
    const size_t zeros = have < GEN_SPEAKER_WINDOW_SAMPLES ? GEN_SPEAKER_WINDOW_SAMPLES - have : 0;
    size_t skip = have > GEN_SPEAKER_WINDOW_SAMPLES ? have - GEN_SPEAKER_WINDOW_SAMPLES : 0;
    memset(v->pcm, 0, zeros * sizeof(int16_t));
    int16_t *to = v->pcm + zeros;
    for (uint32_t h = from; h != last + 1; h++) {
        const size_t off = skip < GEN_GRID_HOP_SAMPLES ? skip : GEN_GRID_HOP_SAMPLES;
        memcpy(to, s.pcm_ring[h % RING_HOPS] + off, (GEN_GRID_HOP_SAMPLES - off) * sizeof(int16_t));
        to += GEN_GRID_HOP_SAMPLES - off;
        skip -= off;
    }
    v->decision = *decision;
    weigh_noise(decision, v);
    return ESP_OK;
}

static void reject_as(svc_listen_decision_t *d, const char *code)
{
    d->event.kind = APP_EVT_REJECT;
    strlcpy(d->event.code, code, sizeof(d->event.code));
}

void svc_listen_busy_reject(svc_listen_decision_t *decision)
{
    if (decision != NULL) { reject_as(decision, APP_CODE_SPEAKER_BUSY); }
}

// Rows stay in slot order, so the lowest row on a tie of scores is the lowest slot (KEHOACH 3.17).
static size_t print_row(uint8_t slot)
{
    speaker_t *k = &s.spk;
    size_t row = 0;
    while (row < k->n_prints && k->print_slot[row] < slot) {
        row++;
    }
    if (row == k->n_prints || k->print_slot[row] != slot) {
        const size_t after = k->n_prints - row;
        memmove(k->prints + (row + 1) * k->dims, k->prints + row * k->dims, after * k->dims * sizeof(float));
        memmove(&k->print_slot[row + 1], &k->print_slot[row], after);
        memmove(&k->means[row + 1], &k->means[row], after * sizeof(float));
        memmove(&k->stds[row + 1], &k->stds[row], after * sizeof(float));
        k->print_slot[row] = slot;
        k->n_prints++;
    }
    return row;
}

static void drop_print(uint8_t slot)
{
    speaker_t *k = &s.spk;
    for (size_t row = 0; row < k->n_prints; row++) {
        if (k->print_slot[row] != slot) { continue; }
        const size_t after = k->n_prints - row - 1;
        memmove(k->prints + row * k->dims, k->prints + (row + 1) * k->dims, after * k->dims * sizeof(float));
        memmove(&k->print_slot[row], &k->print_slot[row + 1], after);
        memmove(&k->means[row], &k->means[row + 1], after * sizeof(float));
        memmove(&k->stds[row], &k->stds[row + 1], after * sizeof(float));
        k->n_prints--;
        return;
    }
}

static esp_err_t save_roster(void)
{
    return sys_storage_set_blob(STORAGE_NS_SPEAKER, STORAGE_KEY_SPK_ROSTER, s.spk.roster,
                                sizeof(*s.spk.roster));
}

// The voiceprint goes to NVS ahead of the roster naming its model, so a cut leaves the old one unused.
static esp_err_t enrol(svc_listen_decision_t *out)
{
    speaker_t *k = &s.spk;
    const uint8_t slot = (uint8_t)k->enrolling;
    reject_as(out, APP_CODE_SPEAKER_ENROLLING);
    strlcpy(out->event.speaker_id, k->roster->slot[slot].id, sizeof(out->event.speaker_id));
    out->event.enrol_count = ++k->enrolled;
    k->serial++;
    if (k->enrolled < GEN_SPEAKER_ENROL_COMMANDS) { return ESP_OK; }
    const size_t row = print_row(slot);
    float *print = k->prints + row * k->dims;
    ai_engine_speaker_voiceprint(k->enrol_units, k->enrolled, print);
    ai_engine_speaker_norm(print, &k->means[row], &k->stds[row]);
    k->roster->slot[slot].model_id = ai_engine_speaker_model_id();
    k->enrolling = NO_SLOT;
    k->enrolled = 0;
    divert();
    char key[STORAGE_NVS_NAME_MAX_BYTES];
    tpl_key(slot, key);
    esp_err_t err = sys_storage_set_blob(STORAGE_NS_SPEAKER, key, print, k->dims * sizeof(float));
    if (err == ESP_OK) { err = save_roster(); }
    ESP_LOGI(TAG, "voiceprint of %s from %d commands, slot %u, saved: %s", k->roster->slot[slot].id,
             GEN_SPEAKER_ENROL_COMMANDS, (unsigned)slot, esp_err_to_name(err));
    return err == ESP_OK ? ESP_OK : APP_ERR_SPEAKERS_NOT_SAVED;
}

esp_err_t svc_listen_verify(uint8_t slot, svc_listen_decision_t *out)
{
    if (out == NULL || !s.ready || s.pcm_ring == NULL || slot >= SVC_LISTEN_VERIFY_SLOTS) {
        return ESP_ERR_INVALID_ARG;
    }
    const verify_slot_t *v = &s.slots[slot];
    speaker_t *k = &s.spk;
    svc_listen_speaker_expire();
    const bool enrolling = k->enrolling != NO_SLOT;
    *out = v->decision;
    if (!enrolling && !k->on) { return ESP_OK; }
    if (!enrolling && k->n_prints == 0) {
        reject_as(out, APP_CODE_SPEAKER_UNENROLLED);
        return ESP_OK;
    }
    const int64_t from_us = esp_timer_get_time();
    float *unit = enrolling ? k->enrol_units + (size_t)k->enrolled * k->dims : k->unit;
    const esp_err_t err = ai_engine_speaker_embed(v->pcm, unit);
    out->verify_us = (uint32_t)(esp_timer_get_time() - from_us);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "speaker check of hop %" PRIu32 ": %s", out->event.seq, esp_err_to_name(err));
        reject_as(out, APP_CODE_SPEAKER_BUSY);
        return ESP_OK;
    }
    if (enrolling) { return enrol(out); }
    size_t best = 0;
    out->speaker_milli_z = ai_engine_speaker_best(unit, k->prints, k->means, k->stds, k->n_prints, &best,
                                                  &out->event.speaker_permille);
    out->event.has_speaker = true;
    strlcpy(out->event.speaker_id, k->roster->slot[k->print_slot[best]].id, sizeof(out->event.speaker_id));
    out->noise_bin =
        ai_engine_speaker_noise_bin(v->lead_energy, v->lead_samples, v->speech_energy, v->speech_samples);
    if (!ai_engine_speaker_passes(out->speaker_milli_z, k->n_prints, out->noise_bin)) {
        reject_as(out, APP_CODE_SPEAKER);
    }
    return ESP_OK;
}

static bool is_speaker_id(const char *id)
{
    const size_t prefix = sizeof(APP_SPEAKER_ID_PREFIX) - 1;
    if (memchr(id, '\0', APP_SPEAKER_ID_BYTES) == NULL || strlen(id) != APP_SPEAKER_ID_BYTES - 1 ||
        strncmp(id, APP_SPEAKER_ID_PREFIX, prefix) != 0) {
        return false;
    }
    for (size_t i = prefix; i < APP_SPEAKER_ID_BYTES - 1; i++) {
        if (id[i] < '0' || id[i] > '9') { return false; }
    }
    return true;
}

static size_t utf8_bytes(uint8_t lead)
{
    if (lead < 0x80) { return 1; }
    if ((lead & 0xE0) == 0xC0) { return 2; }
    if ((lead & 0xF0) == 0xE0) { return 3; }
    return (lead & 0xF8) == 0xF0 ? 4 : 0;
}

// Well-formed UTF-8 of 1 to APP_SPEAKER_NAME_MAX_CHARS characters, as speaker_set.schema.json's name.
static bool is_speaker_name(const char *name)
{
    if (memchr(name, '\0', APP_SPEAKER_NAME_BYTES) == NULL) { return false; }
    size_t chars = 0;
    for (const uint8_t *p = (const uint8_t *)name; *p != 0; chars++) {
        const size_t len = utf8_bytes(*p);
        if (len == 0) { return false; }
        for (size_t i = 1; i < len; i++) {
            if ((p[i] & 0xC0) != 0x80) { return false; }
        }
        p += len;
    }
    return chars >= 1 && chars <= APP_SPEAKER_NAME_MAX_CHARS;
}

static bool is_roster(const app_speaker_set_t *set)
{
    if (set->count > APP_SPEAKERS_MAX) { return false; }
    for (size_t i = 0; i < set->count; i++) {
        if (!is_speaker_id(set->speakers[i].id) || !is_speaker_name(set->speakers[i].name)) { return false; }
        for (size_t j = 0; j < i; j++) {
            if (strcmp(set->speakers[i].id, set->speakers[j].id) == 0) { return false; }
        }
    }
    return true;
}

static int slot_of(const char *id)
{
    for (int slot = 0; slot < APP_SPEAKERS_MAX; slot++) {
        const char *held = s.spk.roster->slot[slot].id;
        if (held[0] != '\0' && strncmp(held, id, APP_SPEAKER_ID_BYTES) == 0) { return slot; }
    }
    return NO_SLOT;
}

static bool listed(const app_speaker_set_t *set, const char *id)
{
    for (size_t i = 0; i < set->count; i++) {
        if (strncmp(set->speakers[i].id, id, APP_SPEAKER_ID_BYTES) == 0) { return true; }
    }
    return false;
}

// One dropped loses its voiceprint in NVS first: a cut then leaves the roster naming them, never their print
// alone.
static esp_err_t drop_speaker(uint8_t slot)
{
    speaker_t *k = &s.spk;
    char key[STORAGE_NVS_NAME_MAX_BYTES];
    tpl_key(slot, key);
    const esp_err_t err = sys_storage_erase(STORAGE_NS_SPEAKER, key);
    drop_print(slot);
    if (k->enrolling == slot) {
        k->enrolling = NO_SLOT;
        k->enrolled = 0;
    }
    ESP_LOGI(TAG, "%s dropped from slot %u, voiceprint erased: %s", k->roster->slot[slot].id, (unsigned)slot,
             esp_err_to_name(err));
    memset(&k->roster->slot[slot], 0, sizeof(k->roster->slot[slot]));
    return err;
}

static void take_speaker_into(const app_speaker_t *who)
{
    storage_speaker_slot_t *slots = s.spk.roster->slot;
    int slot = slot_of(who->id);
    for (int free = 0; slot == NO_SLOT && free < APP_SPEAKERS_MAX; free++) {
        if (slots[free].id[0] == '\0') { slot = free; }
    }
    if (slot == NO_SLOT) { return; }
    if (slots[slot].id[0] == '\0') { strlcpy(slots[slot].id, who->id, sizeof(slots[slot].id)); }
    strlcpy(slots[slot].name, who->name, sizeof(slots[slot].name));
}

esp_err_t svc_listen_speaker_set(const app_speaker_set_t *set)
{
    if (!s.ready || s.pcm_ring == NULL) { return ESP_ERR_NOT_SUPPORTED; }
    if (set == NULL) { return ESP_ERR_INVALID_ARG; }
    if (!is_roster(set)) { return APP_ERR_SPEAKERS_INVALID; }
    speaker_t *k = &s.spk;
    if (set->version <= k->roster->version) {
        ESP_LOGI(TAG, "roster v%" PRIu32 " not above v%" PRIu32 ", ignored", set->version,
                 k->roster->version);
        return ESP_OK;
    }
    esp_err_t err = ESP_OK;
    for (uint8_t slot = 0; slot < APP_SPEAKERS_MAX; slot++) {
        const char *held = k->roster->slot[slot].id;
        if (held[0] != '\0' && !listed(set, held)) {
            const esp_err_t dropped = drop_speaker(slot);
            err = err == ESP_OK ? dropped : err;
        }
    }
    for (size_t i = 0; i < set->count; i++) {
        take_speaker_into(&set->speakers[i]);
    }
    if (k->on != set->check) {
        k->on = set->check;
        const esp_err_t flag = sys_storage_set_u8(STORAGE_NS_SPEAKER, STORAGE_KEY_SPK_CHIP, k->on ? 1 : 0);
        err = err == ESP_OK ? flag : err;
    }
    k->roster->version = set->version;
    const esp_err_t saved = save_roster();
    err = err == ESP_OK ? saved : err;
    k->serial++;
    divert();
    ESP_LOGI(TAG, "roster v%" PRIu32 ": %u people, check %s, %u voiceprints, saved: %s", set->version,
             (unsigned)set->count, k->on ? "on" : "off", (unsigned)k->n_prints, esp_err_to_name(err));
    return err == ESP_OK ? ESP_OK : APP_ERR_SPEAKERS_NOT_SAVED;
}

esp_err_t svc_listen_speaker_enrol(const char *id)
{
    if (!s.ready || s.pcm_ring == NULL) { return ESP_ERR_NOT_SUPPORTED; }
    if (id == NULL) { return ESP_ERR_INVALID_ARG; }
    const int slot = slot_of(id);
    if (slot == NO_SLOT) { return APP_ERR_SPEAKER_UNKNOWN; }
    speaker_t *k = &s.spk;
    k->enrolling = (int8_t)slot;
    k->enrolled = 0;
    k->enrol_until_us = esp_timer_get_time() + k->enrol_window_us;
    k->serial++;
    divert();
    ESP_LOGI(TAG, "enrolling %s, slot %d: the next %d commands within %d s", k->roster->slot[slot].id, slot,
             GEN_SPEAKER_ENROL_COMMANDS, (int)(k->enrol_window_us / US_PER_S));
    return ESP_OK;
}

static void end_enrolment(const char *how)
{
    speaker_t *k = &s.spk;
    ESP_LOGI(TAG, "enrolment of %s %s after %u of %d commands", k->roster->slot[k->enrolling].id, how,
             (unsigned)k->enrolled, GEN_SPEAKER_ENROL_COMMANDS);
    k->enrolling = NO_SLOT;
    k->enrolled = 0;
    k->serial++;
    divert();
}

esp_err_t svc_listen_speaker_enrol_cancel(void)
{
    if (!s.ready || s.pcm_ring == NULL) { return ESP_ERR_NOT_SUPPORTED; }
    if (s.spk.enrolling != NO_SLOT) { end_enrolment("cancelled"); }
    return ESP_OK;
}

void svc_listen_speaker_expire(void)
{
    const speaker_t *k = &s.spk;
    if (s.ready && s.pcm_ring != NULL && k->enrolling != NO_SLOT &&
        esp_timer_get_time() >= k->enrol_until_us) {
        end_enrolment("timed out");
    }
}

esp_err_t svc_listen_speaker_state(app_speaker_state_t *out)
{
    if (out == NULL) { return ESP_ERR_INVALID_ARG; }
    if (!s.ready || s.pcm_ring == NULL) { return ESP_ERR_NOT_SUPPORTED; }
    const speaker_t *k = &s.spk;
    memset(out, 0, sizeof(*out));
    out->serial = k->serial;
    out->version = k->roster->version;
    out->check = k->on;
    for (uint8_t slot = 0; slot < APP_SPEAKERS_MAX; slot++) {
        const storage_speaker_slot_t *who = &k->roster->slot[slot];
        if (who->id[0] == '\0') { continue; }
        strlcpy(out->speakers[out->count].id, who->id, sizeof(out->speakers[out->count].id));
        strlcpy(out->speakers[out->count].name, who->name, sizeof(out->speakers[out->count].name));
        out->enrolled[out->count] = memchr(k->print_slot, slot, k->n_prints) != NULL;
        out->count++;
    }
    if (k->enrolling != NO_SLOT) {
        strlcpy(out->enrolling, k->roster->slot[k->enrolling].id, sizeof(out->enrolling));
        out->enrol_count = k->enrolled;
    }
    return ESP_OK;
}
