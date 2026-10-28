#include <string.h>

#include "dsp_spec/swift_dims.h"
#include "spec_internal.h"

#define DELTA_TAPS 2     // frames each side, as Kaldi's delta
#define DELTA_NORM 10.0f // the sum of j * j over the taps

typedef struct {
    float log_f0;
    float confidence;
} frame_t;

struct dsp_spec_swift_dims_s {
    dsp_spec_swift_dims_config_t cfg;
    int span;        // frames the ring holds: left, the frame given, right
    frame_t *ring;   // frame n at n % span
    uint32_t pushed; // frames taken since the reset
    bool kept;       // a voiced frame has left the ring
    uint32_t kept_at;
    float kept_log_f0; // the newest voiced frame that has left it
};

static bool config_ok(const dsp_spec_swift_dims_config_t *cfg)
{
    return cfg != NULL && cfg->left_frames >= DELTA_TAPS && cfg->right_frames >= DELTA_TAPS;
}

size_t dsp_spec_swift_dims_workspace_bytes(const dsp_spec_swift_dims_config_t *cfg)
{
    if (!config_ok(cfg)) { return 0; }
    const size_t span = (size_t)cfg->left_frames + 1 + (size_t)cfg->right_frames;
    return SPEC_ALIGN_BYTES + spec_round_up(sizeof(struct dsp_spec_swift_dims_s)) +
           spec_round_up(span * sizeof(frame_t));
}

esp_err_t dsp_spec_swift_dims_init(dsp_spec_swift_dims_t **out, const dsp_spec_swift_dims_config_t *cfg,
                                   void *mem, size_t bytes)
{
    if (out == NULL || mem == NULL || !config_ok(cfg)) { return ESP_ERR_INVALID_ARG; }
    if (bytes < dsp_spec_swift_dims_workspace_bytes(cfg)) { return ESP_ERR_INVALID_SIZE; }
    spec_carver_t c = spec_carver(mem, bytes);
    dsp_spec_swift_dims_t *d = spec_carve(&c, sizeof(*d));
    d->cfg = *cfg;
    d->span = cfg->left_frames + 1 + cfg->right_frames;
    d->ring = spec_carve(&c, (size_t)d->span * sizeof(frame_t));
    dsp_spec_swift_dims_reset(d);
    *out = d;
    return ESP_OK;
}

void dsp_spec_swift_dims_reset(dsp_spec_swift_dims_t *dims)
{
    if (dims == NULL) { return; }
    memset(dims->ring, 0, (size_t)dims->span * sizeof(frame_t));
    dims->pushed = 0;
    dims->kept = false;
}

static const frame_t *frame_at(const dsp_spec_swift_dims_t *d, uint32_t n)
{
    return &d->ring[n % (uint32_t)d->span];
}

static bool voiced(const dsp_spec_swift_dims_t *d, const frame_t *f)
{
    return f->confidence >= d->cfg.confidence;
}

// Frame s's log F0 as the frames up to newest show it: its own when voiced, else drawn between the voiced
// frames around it, the one behind held while none ahead is in, the one ahead taken while none behind.
static float log_f0_at(const dsp_spec_swift_dims_t *d, uint32_t s, uint32_t newest)
{
    const frame_t *own = frame_at(d, s);
    if (voiced(d, own)) { return own->log_f0; }
    const uint32_t oldest = newest + 1 >= (uint32_t)d->span ? newest + 1 - (uint32_t)d->span : 0;
    bool before = false;
    uint32_t p = 0;
    float before_log_f0 = 0.0f;
    for (uint32_t u = s; u > oldest && !before; u--) {
        const frame_t *f = frame_at(d, u - 1);
        if (voiced(d, f)) {
            before = true;
            p = u - 1;
            before_log_f0 = f->log_f0;
        }
    }
    if (!before && d->kept) {
        before = true;
        p = d->kept_at;
        before_log_f0 = d->kept_log_f0;
    }
    for (uint32_t v = s + 1; v <= newest; v++) {
        const frame_t *f = frame_at(d, v);
        if (!voiced(d, f)) { continue; }
        if (!before) { return f->log_f0; }
        return before_log_f0 + (f->log_f0 - before_log_f0) * ((float)(s - p) / (float)(v - p));
    }
    return before ? before_log_f0 : 0.0f;
}

static void frame_dims(const dsp_spec_swift_dims_t *d, uint32_t t, uint32_t newest, float *out)
{
    const dsp_spec_swift_dims_config_t *cfg = &d->cfg;
    const uint32_t left = (uint32_t)cfg->left_frames;
    float held = 0.0f;
    float weighted = 0.0f;
    for (uint32_t u = t >= left ? t - left : 0; u <= newest; u++) {
        const frame_t *f = frame_at(d, u);
        if (voiced(d, f)) {
            held += f->confidence;
            weighted += f->confidence * f->log_f0;
        }
    }
    out[1] = held > 0.0f ? (log_f0_at(d, t, newest) - weighted / held) * cfg->pitch_scale : 0.0f;
    const float a = log_f0_at(d, t >= DELTA_TAPS ? t - DELTA_TAPS : 0, newest);
    const float b = log_f0_at(d, t >= 1 ? t - 1 : 0, newest);
    const float c = log_f0_at(d, t + 1, newest);
    const float e = log_f0_at(d, t + DELTA_TAPS, newest);
    out[2] = (2.0f * (e - a) + (c - b)) / DELTA_NORM * cfg->delta_scale;
    out[0] = spec_pov_feature(frame_at(d, t)->confidence) * cfg->pov_scale;
}

esp_err_t dsp_spec_swift_dims_push(dsp_spec_swift_dims_t *dims, float log_f0, float confidence, float *out,
                                   bool *ready)
{
    if (dims == NULL || out == NULL || ready == NULL) { return ESP_ERR_INVALID_ARG; }
    const uint32_t newest = dims->pushed;
    frame_t *slot = &dims->ring[newest % (uint32_t)dims->span];
    if (newest >= (uint32_t)dims->span && voiced(dims, slot)) {
        dims->kept = true;
        dims->kept_at = newest - (uint32_t)dims->span;
        dims->kept_log_f0 = slot->log_f0;
    }
    *slot = (frame_t){log_f0, confidence};
    dims->pushed++;
    *ready = newest >= (uint32_t)dims->cfg.right_frames;
    if (*ready) { frame_dims(dims, newest - (uint32_t)dims->cfg.right_frames, newest, out); }
    return ESP_OK;
}
