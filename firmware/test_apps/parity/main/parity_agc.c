#include <stdlib.h>

#include "dsp_afe/agc.h"
#include "gen_afe.h"
#include "gen_grid.h"
#include "parity.h"

#define PCM_FULL_SCALE 32768.0f

static dsp_afe_agc_t *make_agc(float target_dbfs, void **mem)
{
    const dsp_afe_agc_config_t cfg = {
        .target_dbfs = target_dbfs,
        .gain_min_db = GEN_AFE_AGC_GAIN_MIN_DB,
        .gain_max_db = GEN_AFE_AGC_GAIN_MAX_DB,
        .up_db_per_s = GEN_AFE_AGC_UP_DB_PER_S,
        .down_db_per_s = GEN_AFE_AGC_DOWN_DB_PER_S,
        .limit_dbfs = GEN_AFE_AGC_LIMIT_DBFS,
        .lookahead_ms = GEN_AFE_AGC_LOOKAHEAD_MS,
        .level_tau_s = GEN_AFE_AGC_LEVEL_TAU_S,
        .level_gate_db = GEN_AFE_AGC_LEVEL_GATE_DB,
        .level_fall_db_per_s = GEN_AFE_AGC_LEVEL_FALL_DB_PER_S,
        .release_ms = GEN_AFE_AGC_RELEASE_MS,
    };
    const size_t bytes = dsp_afe_agc_workspace_bytes(&cfg);
    *mem = bytes > 0 ? malloc(bytes) : NULL;
    dsp_afe_agc_t *agc = NULL;
    return *mem != NULL && dsp_afe_agc_init(&agc, &cfg, *mem, bytes) == ESP_OK ? agc : NULL;
}

// out holds every step-th sample of each hop, as the case keeps them.
static bool run_hops(dsp_afe_agc_t *agc, const float *pcm, const float *speech, size_t hops, size_t step,
                     float *out, float *gain_db)
{
    float hop[GEN_GRID_HOP_SAMPLES];
    const size_t kept = GEN_GRID_HOP_SAMPLES / step;
    for (size_t h = 0; h < hops; h++) {
        for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
            hop[i] = pcm[h * GEN_GRID_HOP_SAMPLES + i] / PCM_FULL_SCALE;
        }
        if (dsp_afe_agc_process(agc, hop, speech[h] != 0.0f, &gain_db[h]) != ESP_OK) { return false; }
        for (size_t i = 0; i < kept; i++) {
            out[h * kept + i] = hop[i * step];
        }
    }
    return true;
}

bool parity_agc(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t pcm, speech, config, out, gain;
    if (!parity_tensor(buf, len, "pcm", &pcm) || !parity_tensor(buf, len, "speech", &speech) ||
        !parity_tensor(buf, len, "config", &config) || !parity_tensor(buf, len, "out", &out) ||
        !parity_tensor(buf, len, "gain_db", &gain) || pcm.ndim != 2 || pcm.dims[1] != GEN_GRID_HOP_SAMPLES ||
        out.ndim != 2 || out.dims[0] != pcm.dims[0] || out.dims[1] == 0 ||
        GEN_GRID_HOP_SAMPLES % out.dims[1] != 0) {
        return false;
    }
    const size_t hops = pcm.dims[0];
    const size_t n_out = hops * out.dims[1];
    float target = 0.0f;
    void *mem = NULL;
    float *in = malloc(hops * GEN_GRID_HOP_SAMPLES * sizeof(float));
    float *flags = malloc(hops * sizeof(float));
    float *want = malloc((n_out + hops) * sizeof(float));
    float *got = malloc((n_out + hops) * sizeof(float));
    dsp_afe_agc_t *agc = NULL;
    bool ok = in != NULL && flags != NULL && want != NULL && got != NULL &&
              parity_floats(&config, &target, 1) && parity_floats(&pcm, in, hops * GEN_GRID_HOP_SAMPLES) &&
              parity_floats(&speech, flags, hops) && parity_floats(&out, want, n_out) &&
              parity_floats(&gain, want + n_out, hops);
    ok = ok && (agc = make_agc(target, &mem)) != NULL &&
         run_hops(agc, in, flags, hops, GEN_GRID_HOP_SAMPLES / out.dims[1], got, got + n_out);
    if (ok) {
        parity_report("agc", case_name, "out", want, got, n_out);
        parity_report("agc", case_name, "gain_db", want + n_out, got + n_out, hops);
    }
    free(mem);
    free(got);
    free(want);
    free(flags);
    free(in);
    return ok;
}
