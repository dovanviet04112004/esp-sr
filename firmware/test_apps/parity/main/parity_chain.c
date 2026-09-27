#include <stdlib.h>

#include "dsp_afe.h"
#include "esp_heap_caps.h"
#include "gen_array.h"
#include "gen_grid.h"
#include "parity.h"

typedef enum {
    FIELD_PCM = 0,
    FIELD_SEQ,
    FIELD_DOA_DEG,
    FIELD_DOA_CONF,
    FIELD_VAD,
    FIELD_LEVEL_DBFS,
    FIELD_GAIN_DB,
    FIELD_FLAGS,
    FIELD_COUNT,
} field_t;

static const char *const kFields[FIELD_COUNT] = {
    "pcm", "seq", "doa_deg", "doa_conf", "vad", "level_dbfs", "gain_db", "flags",
};

static float scalar(const dsp_afe_frame_t *f, field_t field)
{
    switch (field) {
    case FIELD_SEQ: return (float)f->seq;
    case FIELD_DOA_DEG: return f->doa_deg;
    case FIELD_DOA_CONF: return f->doa_conf;
    case FIELD_VAD: return f->vad;
    case FIELD_LEVEL_DBFS: return f->level_dbfs;
    case FIELD_GAIN_DB: return f->gain_db;
    case FIELD_FLAGS: return f->flags;
    default: return 0.0f;
    }
}

static void fill(const dsp_afe_frame_t *frames, size_t hops, field_t field, float *out)
{
    for (size_t h = 0; h < hops; h++) {
        if (field != FIELD_PCM) {
            out[h] = scalar(&frames[h], field);
            continue;
        }
        for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
            out[h * GEN_GRID_HOP_SAMPLES + i] = frames[h].pcm[i];
        }
    }
}

enum { CONFIG_NS_FLOOR_DB, CONFIG_AGC_TARGET_DBFS, CONFIG_VAD_AGGRESSIVENESS, CONFIG_COUNT };

// A case without gains is a board never calibrated: calib stays NULL and the chain runs without balance.
static bool read_setup(const void *buf, size_t len, dsp_afe_config_t *cfg, dsp_afe_calib_t *calib)
{
    gold_tensor_t config, gains;
    float settings[CONFIG_COUNT];
    if (!parity_tensor(buf, len, "config", &config) || !parity_floats(&config, settings, CONFIG_COUNT)) {
        return false;
    }
    *cfg = (dsp_afe_config_t){
        .input_format = "MM",
        .spatial = DSP_AFE_SPATIAL_NONE,
        .ns_floor_db = settings[CONFIG_NS_FLOOR_DB],
        .agc_target_dbfs = settings[CONFIG_AGC_TARGET_DBFS],
        .vad_aggressiveness = (uint8_t)settings[CONFIG_VAD_AGGRESSIVENESS],
    };
    if (!parity_tensor(buf, len, "gains", &gains)) { return true; }
    float pairs[2 * GEN_GRID_N_BINS];
    if (!parity_floats(&gains, pairs, 2 * GEN_GRID_N_BINS)) { return false; }
    for (size_t k = 0; k < GEN_GRID_N_BINS; k++) {
        calib->balance[k] = (dsp_spec_cplx_t){pairs[2 * k], pairs[2 * k + 1]};
    }
    cfg->calib = calib;
    return true;
}

static bool run_frames(const dsp_afe_config_t *cfg, const gold_tensor_t *input, const gold_tensor_t *reset,
                       dsp_afe_frame_t *frames)
{
    size_t hot = 0;
    size_t cold = 0;
    dsp_afe_t *afe = NULL;
    if (dsp_afe_workspace_bytes(cfg, &hot, &cold) != ESP_OK || cold != 0) { return false; }
    void *mem = malloc(hot);
    bool ok = mem != NULL && dsp_afe_init(&afe, cfg, mem, hot, NULL, 0) == ESP_OK;
    const int16_t *in = input->data;
    const uint8_t *resets = reset->data;
    for (size_t h = 0; ok && h < input->dims[0]; h++) {
        if (resets[h] != 0) { dsp_afe_reset(afe); }
        ok = dsp_afe_feed(afe, in + h * GEN_GRID_HOP_SAMPLES * GEN_ARRAY_N_MICS, 1) == ESP_OK &&
             dsp_afe_fetch(afe, &frames[h]) == ESP_OK;
    }
    free(mem);
    return ok;
}

static bool run_chain(const char *block, const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t input, reset, want[FIELD_COUNT];
    if (!parity_tensor(buf, len, "input", &input) || !parity_tensor(buf, len, "reset", &reset) ||
        input.dtype != GOLD_I16 || reset.dtype != GOLD_U8 || input.ndim != 2 ||
        input.dims[1] != GEN_GRID_HOP_SAMPLES * GEN_ARRAY_N_MICS || reset.dims[0] != input.dims[0]) {
        return false;
    }
    for (size_t f = 0; f < FIELD_COUNT; f++) {
        if (!parity_tensor(buf, len, kFields[f], &want[f])) { return false; }
    }
    dsp_afe_config_t cfg;
    dsp_afe_calib_t calib = {0};
    if (!read_setup(buf, len, &cfg, &calib)) { return false; }
    const size_t hops = input.dims[0];
    dsp_afe_frame_t *frames = heap_caps_malloc(hops * sizeof(dsp_afe_frame_t), MALLOC_CAP_SPIRAM);
    float *got = heap_caps_malloc(hops * GEN_GRID_HOP_SAMPLES * sizeof(float), MALLOC_CAP_SPIRAM);
    float *ref = heap_caps_malloc(hops * GEN_GRID_HOP_SAMPLES * sizeof(float), MALLOC_CAP_SPIRAM);
    bool ok = frames != NULL && got != NULL && ref != NULL && run_frames(&cfg, &input, &reset, frames);
    for (size_t f = 0; ok && f < FIELD_COUNT; f++) {
        const size_t n = f == FIELD_PCM ? hops * GEN_GRID_HOP_SAMPLES : hops;
        ok = parity_floats(&want[f], ref, n);
        fill(frames, hops, (field_t)f, got);
        if (ok) { parity_report(block, case_name, kFields[f], ref, got, n); }
    }
    free(frames);
    free(got);
    free(ref);
    return ok;
}

bool parity_chain(const char *case_name, const void *buf, size_t len)
{
    return run_chain("chain", case_name, buf, len);
}

bool parity_chain_modules(const char *case_name, const void *buf, size_t len)
{
    return run_chain("chain_modules", case_name, buf, len);
}
