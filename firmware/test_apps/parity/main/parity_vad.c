#include <stdlib.h>

#include "dsp_afe/vad.h"
#include "gen_grid.h"
#include "parity.h"

#define PCM_FULL_SCALE 32768.0f
#define CONFIG_VALUES 2 // aggressiveness, hangover_ms

typedef struct {
    float *features;
    float *raw;
    float *speech;
} vad_out_t;

static bool run_hops(const float *config, const float *pcm, size_t hops, vad_out_t *out)
{
    const dsp_afe_vad_config_t cfg = {.aggressiveness = (uint8_t)config[0],
                                      .hangover_ms = (uint16_t)config[1]};
    const size_t bytes = dsp_afe_vad_workspace_bytes(&cfg);
    void *mem = bytes > 0 ? malloc(bytes) : NULL;
    dsp_afe_vad_t *vad = NULL;
    float hop[GEN_GRID_HOP_SAMPLES];
    bool ok = mem != NULL && dsp_afe_vad_init(&vad, &cfg, mem, bytes) == ESP_OK;
    for (size_t h = 0; ok && h < hops; h++) {
        for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
            hop[i] = pcm[h * GEN_GRID_HOP_SAMPLES + i] / PCM_FULL_SCALE;
        }
        bool speech = false;
        dsp_afe_vad_detail_t detail;
        ok = dsp_afe_vad_process(vad, hop, &speech) == ESP_OK;
        dsp_afe_vad_detail(vad, &detail);
        for (size_t b = 0; b < GEN_AFE_VAD_BANDS; b++) {
            out->features[h * GEN_AFE_VAD_BANDS + b] = detail.level_db[b];
        }
        out->raw[h] = detail.raw ? 1.0f : 0.0f;
        out->speech[h] = speech ? 1.0f : 0.0f;
    }
    free(mem);
    return ok;
}

bool parity_vad(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t pcm, config, features, raw, speech;
    if (!parity_tensor(buf, len, "pcm", &pcm) || !parity_tensor(buf, len, "config", &config) ||
        !parity_tensor(buf, len, "features", &features) || !parity_tensor(buf, len, "raw", &raw) ||
        !parity_tensor(buf, len, "speech", &speech) || pcm.ndim != 2 || pcm.dims[1] != GEN_GRID_HOP_SAMPLES) {
        return false;
    }
    const size_t hops = pcm.dims[0];
    const size_t n_features = hops * GEN_AFE_VAD_BANDS;
    float cfg[CONFIG_VALUES];
    float *in = malloc(hops * GEN_GRID_HOP_SAMPLES * sizeof(float));
    float *want = malloc((n_features + 2 * hops) * sizeof(float));
    float *got = malloc((n_features + 2 * hops) * sizeof(float));
    vad_out_t out = {.features = got, .raw = got + n_features, .speech = got + n_features + hops};
    const bool ok = in != NULL && want != NULL && got != NULL && parity_floats(&config, cfg, CONFIG_VALUES) &&
                    parity_floats(&pcm, in, hops * GEN_GRID_HOP_SAMPLES) &&
                    parity_floats(&features, want, n_features) &&
                    parity_floats(&raw, want + n_features, hops) &&
                    parity_floats(&speech, want + n_features + hops, hops) && run_hops(cfg, in, hops, &out);
    if (ok) {
        parity_report("vad", case_name, "features", want, out.features, n_features);
        parity_report("vad", case_name, "raw", want + n_features, out.raw, hops);
        parity_report("vad", case_name, "speech", want + n_features + hops, out.speech, hops);
    }
    free(got);
    free(want);
    free(in);
    return ok;
}
