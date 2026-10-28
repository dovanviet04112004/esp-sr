#include <stdlib.h>

#include "dsp_spec.h"
#include "parity.h"

#define CONFIG_FLOATS 4 // the voiced threshold, then the three scales
#define CONFIG_INTS 2   // the frames behind and ahead

static bool config_of(const void *buf, size_t len, dsp_spec_swift_dims_config_t *cfg)
{
    gold_tensor_t floats, ints;
    float f[CONFIG_FLOATS];
    float i[CONFIG_INTS];
    if (!parity_tensor(buf, len, "config_f", &floats) || floats.dims[0] != CONFIG_FLOATS ||
        !parity_tensor(buf, len, "config_i", &ints) || ints.dims[0] != CONFIG_INTS ||
        !parity_floats(&floats, f, CONFIG_FLOATS) || !parity_floats(&ints, i, CONFIG_INTS)) {
        return false;
    }
    *cfg = (dsp_spec_swift_dims_config_t){.confidence = f[0],
                                          .left_frames = (int)i[0],
                                          .right_frames = (int)i[1],
                                          .pov_scale = f[1],
                                          .pitch_scale = f[2],
                                          .delta_scale = f[3]};
    return true;
}

bool parity_swift_dims(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t log_f0, confidence, reset, out, ready;
    dsp_spec_swift_dims_config_t cfg;
    if (!parity_tensor(buf, len, "log_f0", &log_f0) || log_f0.dtype != GOLD_F32 ||
        !parity_tensor(buf, len, "confidence", &confidence) || confidence.dtype != GOLD_F32 ||
        !parity_tensor(buf, len, "reset", &reset) || !parity_tensor(buf, len, "out", &out) ||
        !parity_tensor(buf, len, "ready", &ready) || !config_of(buf, len, &cfg)) {
        return false;
    }
    const size_t frames = log_f0.dims[0];
    if (confidence.dims[0] != frames || reset.dims[0] != frames || ready.dims[0] != frames ||
        out.dims[0] != frames || out.dims[1] != DSP_SPEC_SWIFT_DIMS) {
        return false;
    }
    const size_t bytes = dsp_spec_swift_dims_workspace_bytes(&cfg);
    void *mem = malloc(bytes);
    float *flags = malloc(2 * frames * sizeof(float));
    float *got = calloc(frames * DSP_SPEC_SWIFT_DIMS, sizeof(float));
    dsp_spec_swift_dims_t *dims = NULL;
    bool ok = bytes > 0 && mem != NULL && flags != NULL && got != NULL &&
              parity_floats(&reset, flags, frames) && parity_floats(&ready, flags + frames, frames) &&
              dsp_spec_swift_dims_init(&dims, &cfg, mem, bytes) == ESP_OK;
    const float *log_f0s = log_f0.data;
    const float *confidences = confidence.data;
    for (size_t t = 0; ok && t < frames; t++) {
        if (flags[t] != 0.0f) { dsp_spec_swift_dims_reset(dims); }
        bool given = false;
        ok = dsp_spec_swift_dims_push(dims, log_f0s[t], confidences[t], got + t * DSP_SPEC_SWIFT_DIMS,
                                      &given) == ESP_OK &&
             given == (flags[frames + t] != 0.0f);
    }
    if (ok) { parity_report("swift_dims", case_name, "out", out.data, got, frames * DSP_SPEC_SWIFT_DIMS); }
    free(mem);
    free(flags);
    free(got);
    return ok;
}
