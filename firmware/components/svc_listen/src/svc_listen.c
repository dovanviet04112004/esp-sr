#include "svc_listen.h"

#include <inttypes.h>
#include <stdatomic.h>
#include <string.h>

#include "ai_engine.h"
#include "app_err.h"
#include "dsp_spec/fft.h"
#include "dsp_spec/mel.h"
#include "dsp_spec/pitch.h"
#include "dsp_spec/stft.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "gen_grid.h"
#include "gen_listen.h"
#include "gen_speaker.h"
#include "storage_format.h"
#include "sys_storage.h"

#define FEATURES (GEN_LISTEN_N_BANDS + DSP_SPEC_PITCH_FEATURES)
#define WINDOWS_MAX 4
// A queued window whose first hop the ring passes is dropped, never read torn.
#define RING_HOPS 512
#define LEAD_HOPS GEN_LISTEN_UTTERANCE_LEAD_HOPS
#define WINDOW_HOPS GEN_LISTEN_WINDOW_HOPS
#define PCM_FULL_SCALE 32768.0f // srpipe's to_float: int16 over 2^15, exact
#define PERMILLE_MAX 1000       // event.schema caps score and margin here
#define MEM_ALIGN 16
#define TAIL_HOPS ((GEN_SPEAKER_WINDOW_SAMPLES + GEN_GRID_HOP_SAMPLES - 1) / GEN_GRID_HOP_SAMPLES)

_Static_assert(AI_ENGINE_VARIANTS_MAX == LANG_VI_VARIANTS_MAX, "one command's readings fit one lexicon row");
_Static_assert(RING_HOPS >= 2 * WINDOW_HOPS, "the ring holds the window worked and the next one whole");
_Static_assert(RING_HOPS > TAIL_HOPS, "the PCM ring holds a speaker window");

typedef struct {
    uint32_t first, last; // hops, both in; last moves on while it is open
    uint32_t floor;       // no window reaches back past this hop
    bool open;            // its utterance still runs
    bool cut_back;        // outgrew WINDOW_HOPS: worked from its end once closed
    bool holed;           // seq broke while open: decided FRAME_GAP, never scored
    int64_t closed_us;
} window_t;

typedef struct {
    void *fft, *stft, *mel, *pitch;
} workspaces_t;

typedef struct {
    int16_t pcm[GEN_SPEAKER_WINDOW_SAMPLES];
    svc_listen_decision_t decision;
} verify_slot_t;

typedef struct {
    bool on;          // NVS speaker/chip
    bool has_print;   // a voiceprint of the loaded speaker entry
    bool enrolling;   // the next commands enrol
    uint8_t enrolled; // of GEN_SPEAKER_ENROL_COMMANDS
    size_t dims;
    float *unit, *voiceprint, *enrol_units;
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
    dsp_spec_pitch_t *pitch;
    float *hop;
    dsp_spec_cplx_t *bins;
    float (*log_mel)[GEN_LISTEN_N_BANDS];
    float (*pitch_ring)[DSP_SPEC_PITCH_FEATURES];
    table_t tables[2]; // the one in use, and a spare to read a new set into
    size_t active;
    lang_vi_dialect_t dialects;
    uint16_t reject, margin, syllable;
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
    s.spk.unit = take(s.spk.dims * sizeof(float));
    s.spk.voiceprint = take(s.spk.dims * sizeof(float));
    s.spk.enrol_units = take(GEN_SPEAKER_ENROL_COMMANDS * s.spk.dims * sizeof(float));
    return s.pcm_ring != NULL && s.slots != NULL && s.spk.unit != NULL && s.spk.voiceprint != NULL &&
           s.spk.enrol_units != NULL;
}

// A voiceprint enrolled on another speaker entry lies in another space, so it stays unused (KEHOACH 3.17).
static void load_speaker(void)
{
    speaker_t *k = &s.spk;
    uint8_t on = 0;
    uint32_t model = 0;
    k->on = sys_storage_get_u8(STORAGE_NS_SPEAKER, STORAGE_KEY_SPK_CHIP, &on) == ESP_OK && on != 0;
    k->has_print = sys_storage_get_u32(STORAGE_NS_SPEAKER, STORAGE_KEY_SPK_TPL_MODEL, &model) == ESP_OK &&
                   model == ai_engine_speaker_model_id() &&
                   sys_storage_get_blob(STORAGE_NS_SPEAKER, STORAGE_KEY_SPK_TPL, k->voiceprint,
                                        k->dims * sizeof(float)) == ESP_OK;
    atomic_store(&s_diverting, k->on);
    ESP_LOGI(TAG, "speaker check %s, voiceprint %s", k->on ? "on" : "off",
             k->has_print ? "of this model" : "none of this model");
}

static void release(workspaces_t *w)
{
    void *const blocks[] = {s.pcm_ring,
                            s.slots,
                            s.spk.unit,
                            s.spk.voiceprint,
                            s.spk.enrol_units,
                            s.hop,
                            s.bins,
                            s.log_mel,
                            s.pitch_ring,
                            s.tables[0].units,
                            s.tables[0].ids,
                            s.tables[0].lexicon,
                            s.tables[1].units,
                            s.tables[1].ids,
                            s.tables[1].lexicon,
                            w->fft,
                            w->stft,
                            w->mel,
                            w->pitch};
    for (size_t i = 0; i < sizeof(blocks) / sizeof(blocks[0]); i++) {
        heap_caps_free(blocks[i]);
    }
    memset(&s, 0, sizeof(s));
    atomic_store(&s_diverting, false);
}

static esp_err_t front_end(workspaces_t *w)
{
    const dsp_spec_mel_config_t mel_cfg = GEN_LISTEN_MEL_CONFIG;
    const dsp_spec_pitch_config_t pitch_cfg = GEN_LISTEN_PITCH_CONFIG;
    const size_t fft_bytes = dsp_spec_fft_workspace_bytes(GEN_GRID_FFT_SIZE);
    const size_t stft_bytes = dsp_spec_stft_workspace_bytes();
    const size_t mel_bytes = dsp_spec_mel_workspace_bytes(&mel_cfg);
    const size_t pitch_bytes = dsp_spec_pitch_workspace_bytes(&pitch_cfg);
    w->fft = take(fft_bytes);
    w->stft = take(stft_bytes);
    w->mel = take(mel_bytes);
    w->pitch = take(pitch_bytes);
    if (w->fft == NULL || w->stft == NULL || w->mel == NULL || w->pitch == NULL) { return ESP_ERR_NO_MEM; }
    dsp_spec_fft_t *fft = NULL;
    esp_err_t err = dsp_spec_fft_init(&fft, GEN_GRID_FFT_SIZE, w->fft, fft_bytes);
    if (err == ESP_OK) { err = dsp_spec_stft_init(&s.stft, fft, w->stft, stft_bytes); }
    if (err == ESP_OK) { err = dsp_spec_mel_init(&s.mel, &mel_cfg, w->mel, mel_bytes); }
    if (err == ESP_OK) { err = dsp_spec_pitch_init(&s.pitch, &pitch_cfg, w->pitch, pitch_bytes); }
    return err;
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
    if (cfg == NULL || !well_formed(&cfg->commands)) { return ESP_ERR_INVALID_ARG; }
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
    s.queue[(s.head + s.count) % WINDOWS_MAX] =
        (window_t){.first = lead > s.after ? lead : s.after, .last = seq, .floor = s.after, .open = true};
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

esp_err_t svc_listen_feed(const int16_t *pcm, uint32_t seq, bool vad, bool gap)
{
    if (!s.ready) { return ESP_ERR_INVALID_STATE; }
    if (pcm == NULL) { return ESP_ERR_INVALID_ARG; }
    if (!s.started || seq != s.next_seq || gap) {
        dsp_spec_stft_reset(s.stft);
        dsp_spec_pitch_reset(s.pitch);
        hole_open();
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
    if (err == ESP_OK) { err = dsp_spec_pitch_frame(s.pitch, s.hop, s.pitch_ring[slot]); }
    if (err != ESP_OK) { return err; }
    if (s.in_run && seq - s.run_last > GEN_LISTEN_UTTERANCE_GAP_HOPS) { close_utterance(); }
    if (vad) {
        if (!s.in_run) { err = open_utterance(seq); }
        s.in_run = true;
        s.run_last = seq;
    }
    extend_open(seq);
    return err;
}

// From the first vad hop, even of a click the close then drops: catching up is the costly part (KEHOACH 5.4).
static bool workable(const window_t *w)
{
    return !w->open || !w->cut_back;
}

bool svc_listen_pending(void)
{
    if (!s.ready || s.count == 0) { return false; }
    const window_t *w = &s.queue[s.head];
    return workable(w) && (!w->open || !s.working || s.at <= w->last);
}

bool svc_listen_busy(void)
{
    return s.ready && (s.count > 0 || s.in_run);
}

static void done(void)
{
    s.working = false;
    s.head = (s.head + 1) % WINDOWS_MAX;
    s.count--;
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
    out->free_gap_permille = r->free_gap_permille;
    out->syllable_gap_permille = r->syllable_gap_permille;
    out->work_us = (uint32_t)s.work_us;
    out->close_us = (uint32_t)(esp_timer_get_time() - w->closed_us);
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

static void enrol(svc_listen_decision_t *out)
{
    speaker_t *k = &s.spk;
    reject_as(out, APP_CODE_SPEAKER_ENROLLING);
    if (++k->enrolled < GEN_SPEAKER_ENROL_COMMANDS) { return; }
    ai_engine_speaker_voiceprint(k->enrol_units, k->enrolled, k->voiceprint);
    k->enrolling = false;
    k->enrolled = 0;
    k->has_print = true;
    esp_err_t err =
        sys_storage_set_blob(STORAGE_NS_SPEAKER, STORAGE_KEY_SPK_TPL, k->voiceprint, k->dims * sizeof(float));
    if (err == ESP_OK) {
        err =
            sys_storage_set_u32(STORAGE_NS_SPEAKER, STORAGE_KEY_SPK_TPL_MODEL, ai_engine_speaker_model_id());
    }
    atomic_store(&s_diverting, k->on);
    ESP_LOGI(TAG, "voiceprint of %d commands enrolled, saved: %s", GEN_SPEAKER_ENROL_COMMANDS,
             esp_err_to_name(err));
}

void svc_listen_verify(uint8_t slot, svc_listen_decision_t *out)
{
    if (out == NULL || !s.ready || s.pcm_ring == NULL || slot >= SVC_LISTEN_VERIFY_SLOTS) { return; }
    const verify_slot_t *v = &s.slots[slot];
    speaker_t *k = &s.spk;
    *out = v->decision;
    if (!k->enrolling && !k->on) { return; }
    if (!k->enrolling && !k->has_print) {
        reject_as(out, APP_CODE_SPEAKER_UNENROLLED);
        return;
    }
    const int64_t from_us = esp_timer_get_time();
    float *unit = k->enrolling ? k->enrol_units + (size_t)k->enrolled * k->dims : k->unit;
    const esp_err_t err = ai_engine_speaker_embed(v->pcm, unit);
    out->verify_us = (uint32_t)(esp_timer_get_time() - from_us);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "speaker check of hop %" PRIu32 ": %s", out->event.seq, esp_err_to_name(err));
        reject_as(out, APP_CODE_SPEAKER_BUSY);
        return;
    }
    if (k->enrolling) {
        enrol(out);
        return;
    }
    out->event.speaker_permille = ai_engine_speaker_score(unit, k->voiceprint);
    out->event.has_speaker = true;
    if (!ai_engine_speaker_passes(out->event.speaker_permille, 1)) { reject_as(out, APP_CODE_SPEAKER); }
}

esp_err_t svc_listen_speaker(svc_listen_speaker_ctl_t ctl)
{
    if (!s.ready || s.pcm_ring == NULL) { return ESP_ERR_NOT_SUPPORTED; }
    speaker_t *k = &s.spk;
    esp_err_t err = ESP_OK;
    switch (ctl) {
    case SVC_LISTEN_SPEAKER_ENROL:
        k->enrolling = true;
        k->enrolled = 0;
        break;
    case SVC_LISTEN_SPEAKER_ON:
    case SVC_LISTEN_SPEAKER_OFF:
        k->on = ctl == SVC_LISTEN_SPEAKER_ON;
        err = sys_storage_set_u8(STORAGE_NS_SPEAKER, STORAGE_KEY_SPK_CHIP, k->on ? 1 : 0);
        break;
    case SVC_LISTEN_SPEAKER_CLEAR:
        k->has_print = false;
        err = sys_storage_erase(STORAGE_NS_SPEAKER, STORAGE_KEY_SPK_TPL);
        if (err == ESP_OK) { err = sys_storage_erase(STORAGE_NS_SPEAKER, STORAGE_KEY_SPK_TPL_MODEL); }
        break;
    default: return ESP_ERR_INVALID_ARG;
    }
    atomic_store(&s_diverting, k->on || k->enrolling);
    ESP_LOGI(TAG, "speaker check %s%s, voiceprint %s: %s", k->on ? "on" : "off",
             k->enrolling ? ", enrolling" : "", k->has_print ? "kept" : "none", esp_err_to_name(err));
    return err;
}
