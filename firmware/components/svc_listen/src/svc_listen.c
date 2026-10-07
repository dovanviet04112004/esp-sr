#include "svc_listen.h"

#include <inttypes.h>
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

#define FEATURES (GEN_LISTEN_N_BANDS + DSP_SPEC_PITCH_FEATURES)
#define WINDOWS_MAX 4
// A queued window whose first hop the ring passes is dropped, never read torn.
#define RING_HOPS 512
#define LEAD_HOPS GEN_LISTEN_UTTERANCE_LEAD_HOPS
#define WINDOW_HOPS GEN_LISTEN_WINDOW_HOPS
#define PCM_FULL_SCALE 32768.0f // srpipe's to_float: int16 over 2^15, exact
#define PERMILLE_MAX 1000       // event.schema caps score and margin here
#define MEM_ALIGN 16

_Static_assert(AI_ENGINE_VARIANTS_MAX == LANG_VI_VARIANTS_MAX, "one command's readings fit one lexicon row");
_Static_assert(RING_HOPS >= 2 * WINDOW_HOPS, "the ring holds the window worked and the next one whole");

typedef struct {
    uint32_t first, last; // hops, both in; last moves on while it is open
    uint32_t floor;       // no window reaches back past this hop
    bool open;            // its utterance still runs
    bool cut_back;        // outgrew WINDOW_HOPS: worked from its end once closed
    int64_t closed_us;
} window_t;

typedef struct {
    void *fft, *stft, *mel, *pitch;
} workspaces_t;

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
    uint16_t reject, margin;
    bool started, in_run, has_open, working;
    uint32_t next_seq, after, run_first, run_last, at;
    window_t queue[WINDOWS_MAX];
    size_t head, count;
    int64_t work_us;
} s;

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

static void release(workspaces_t *w)
{
    void *const blocks[] = {s.hop,
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
    esp_err_t err = s.hop != NULL && s.bins != NULL && rings && tables ? front_end(&w) : ESP_ERR_NO_MEM;
    uint16_t unreadable = 0;
    s.dialects = cfg->dialects;
    if (err == ESP_OK) { err = build_lexicon(&cfg->commands, &s.tables[0], &unreadable); }
    if (err == ESP_OK) { err = ai_engine_command_prepare(s.tables[0].lexicon); }
    if (err != ESP_OK) {
        release(&w);
        return err;
    }
    s.active = 0;
    s.reject = cfg->reject_permille;
    s.margin = cfg->margin_permille;
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
        drop_open();
        s.in_run = false;
        s.after = seq;
    }
    s.started = true;
    s.next_seq = seq + 1;
    const size_t slot = seq % RING_HOPS;
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
        // ctc_score.c's order: far from the free loop, then too close to the second, then a part.
        const char *code = r->free_gap_permille > s.reject ? APP_CODE_LOW_SCORE
                           : r->margin_permille < s.margin ? APP_CODE_LOW_MARGIN
                                                           : APP_CODE_PART;
        strlcpy(e->code, code, sizeof(e->code));
    }
    out->first_seq = w->first;
    out->free_gap_permille = r->free_gap_permille;
    out->work_us = (uint32_t)s.work_us;
    out->close_us = (uint32_t)(esp_timer_get_time() - w->closed_us);
}

bool svc_listen_work(svc_listen_decision_t *out)
{
    if (!svc_listen_pending() || out == NULL) { return false; }
    const window_t *w = &s.queue[s.head];
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
