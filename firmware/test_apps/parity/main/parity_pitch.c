#include <stdlib.h>

#include "dsp_spec.h"
#include "esp_heap_caps.h"
#include "gen_grid.h"
#include "parity.h"

enum {
    CONFIG_RESAMPLE = 0,
    CONFIG_LOWPASS,
    CONFIG_LOWPASS_ZEROS,
    CONFIG_UPSAMPLE_ZEROS,
    CONFIG_WINDOW,
    CONFIG_MIN_F0,
    CONFIG_MAX_F0,
    CONFIG_SOFT_MIN_F0,
    CONFIG_PENALTY,
    CONFIG_DELTA_PITCH,
    CONFIG_BALLAST,
    CONFIG_NORMALIZATION,
    CONFIG_DELTA_WINDOW,
    CONFIG_POV_SCALE,
    CONFIG_PITCH_SCALE,
    CONFIG_DELTA_SCALE,
    CONFIG_COUNT
};

enum { RAW_NCCF = 0, RAW_F0, RAW_COUNT };

static dsp_spec_pitch_config_t config_of(const float *c)
{
    return (dsp_spec_pitch_config_t){
        .resample_hz = c[CONFIG_RESAMPLE],
        .lowpass_cutoff_hz = c[CONFIG_LOWPASS],
        .lowpass_zeros = (uint16_t)c[CONFIG_LOWPASS_ZEROS],
        .upsample_zeros = (uint16_t)c[CONFIG_UPSAMPLE_ZEROS],
        .window_s = c[CONFIG_WINDOW],
        .min_f0_hz = c[CONFIG_MIN_F0],
        .max_f0_hz = c[CONFIG_MAX_F0],
        .soft_min_f0 = c[CONFIG_SOFT_MIN_F0],
        .penalty_factor = c[CONFIG_PENALTY],
        .delta_pitch = c[CONFIG_DELTA_PITCH],
        .nccf_ballast = c[CONFIG_BALLAST],
        .normalization_left_s = c[CONFIG_NORMALIZATION],
        .delta_window = (uint16_t)c[CONFIG_DELTA_WINDOW],
        .pov_scale = c[CONFIG_POV_SCALE],
        .pitch_scale = c[CONFIG_PITCH_SCALE],
        .delta_pitch_scale = c[CONFIG_DELTA_SCALE],
    };
}

bool parity_pitch(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t config, pcm, reset, features, raw;
    if (!parity_tensor(buf, len, "config", &config) || config.dims[0] != CONFIG_COUNT ||
        !parity_tensor(buf, len, "pcm", &pcm) || !parity_tensor(buf, len, "reset", &reset) ||
        !parity_tensor(buf, len, "features", &features) || !parity_tensor(buf, len, "raw", &raw)) {
        return false;
    }
    const size_t hops = reset.dims[0];
    if (pcm.dims[0] != hops * GEN_GRID_HOP_SAMPLES || features.dims[0] != hops ||
        features.dims[1] != DSP_SPEC_PITCH_FEATURES || raw.dims[0] != hops || raw.dims[1] != RAW_COUNT) {
        return false;
    }
    const dsp_spec_pitch_config_t cfg = config_of(config.data);
    const size_t bytes = dsp_spec_pitch_workspace_bytes(&cfg);
    void *mem = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM);
    float *flags = malloc(hops * sizeof(float));
    float *got = heap_caps_malloc(hops * DSP_SPEC_PITCH_FEATURES * sizeof(float), MALLOC_CAP_SPIRAM);
    float *got_raw = heap_caps_malloc(hops * RAW_COUNT * sizeof(float), MALLOC_CAP_SPIRAM);
    dsp_spec_pitch_t *tracker = NULL;
    bool ok = bytes > 0 && mem != NULL && flags != NULL && got != NULL && got_raw != NULL &&
              parity_floats(&reset, flags, hops) && dsp_spec_pitch_init(&tracker, &cfg, mem, bytes) == ESP_OK;
    const float *samples = pcm.data;
    for (size_t h = 0; ok && h < hops; h++) {
        if (flags[h] != 0.0f) { dsp_spec_pitch_reset(tracker); }
        ok = dsp_spec_pitch_frame(tracker, samples + h * GEN_GRID_HOP_SAMPLES,
                                  got + h * DSP_SPEC_PITCH_FEATURES) == ESP_OK;
        dsp_spec_pitch_latest(tracker, got_raw + h * RAW_COUNT + RAW_NCCF, got_raw + h * RAW_COUNT + RAW_F0);
    }
    if (ok) {
        parity_report("pitch", case_name, "features", features.data, got, hops * DSP_SPEC_PITCH_FEATURES);
        parity_report("pitch", case_name, "raw", raw.data, got_raw, hops * RAW_COUNT);
    }
    free(mem);
    free(flags);
    free(got);
    free(got_raw);
    return ok;
}
