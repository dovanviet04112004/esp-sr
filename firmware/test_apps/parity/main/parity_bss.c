#include <math.h>
#include <stdlib.h>

#include "dsp_afe/bss.h"
#include "gen_grid.h"
#include "parity.h"

#define FLOATS_PER_BIN 2
#define CONFIG_VALUES 25 // in the order of srpipe.dsp.emit_golden.BSS_FIELDS
#define DIRECTIONS 2
#define NUMBERS DSP_AFE_BSS_SEATS_MAX

// Per hop: gate, seats, directions, every number's gate and degrees, the delayed gates and kept number.
enum { AT_GATE, AT_SEATS, AT_DIRS, AT_NUMBER_GATE, AT_NUMBER_DEG, AT_BACK_GATE, AT_BACK_KEPT, AT_COUNT };
static const size_t kWidth[AT_COUNT] = {1, 1, DIRECTIONS, NUMBERS, NUMBERS, NUMBERS, 1};
static const char *const kNames[AT_COUNT] = {"gate",       "seats",     "directions", "number_gate",
                                             "number_deg", "back_gate", "back_kept"};

static dsp_afe_bss_config_t config_of(const float *v)
{
    return (dsp_afe_bss_config_t){.mode = (dsp_afe_bss_mode_t)v[0],
                                  .keep_rule = (dsp_afe_bss_keep_t)v[1],
                                  .keep_deg = v[2],
                                  .gate_slope = v[3],
                                  .conf_min = (uint8_t)v[4],
                                  .seats_max = (uint8_t)v[5],
                                  .decision_s = v[6],
                                  .recheck_s = v[7],
                                  .first_speech_s = v[8],
                                  .confirm = (uint8_t)v[9],
                                  .occupancy_min = v[10],
                                  .min_gap = v[11],
                                  .bins_per_gap = (uint8_t)v[12],
                                  .smooth_bins = (uint8_t)v[13],
                                  .prominence = v[14],
                                  .core_share = v[15],
                                  .seat_follow = v[16],
                                  .boundary_share_min = v[17],
                                  .boundary_follow = v[18],
                                  .seat_forget_s = v[19],
                                  .window_horizon_s = v[20],
                                  .update_every_hops = (uint8_t)v[21],
                                  .spacing_m = v[22],
                                  .speed_of_sound_m_s = v[23],
                                  .history_s = v[24]};
}

// The mirror's own reach, Bss.history: the delayed gates are read that far back.
static uint32_t history_hops(const dsp_afe_bss_config_t *cfg)
{
    return (uint32_t)floor((double)cfg->history_s / ((double)GEN_GRID_HOP_SAMPLES / GEN_GRID_SAMPLE_RATE_HZ) +
                           0.5);
}

static size_t offset_of(size_t field, size_t hops)
{
    size_t at = 0;
    for (size_t f = 0; f < field; f++) {
        at += kWidth[f] * hops;
    }
    return at;
}

// got holds every per-hop field, field after field; last both outputs of the last hop.
static bool run_hops(const float *config, const float *bins, const float *angle, const float *confidence,
                     const float *searched, size_t hops, float *got, float *last)
{
    const dsp_afe_bss_config_t cfg = config_of(config);
    const uint32_t back = history_hops(&cfg);
    const size_t bytes = dsp_afe_bss_workspace_bytes(&cfg);
    void *mem = bytes > 0 ? malloc(bytes) : NULL;
    dsp_afe_bss_t *bss = NULL;
    bool ok = mem != NULL && dsp_afe_bss_init(&bss, &cfg, mem, bytes) == ESP_OK;
    const size_t stride = GEN_GRID_N_BINS * FLOATS_PER_BIN;
    for (size_t h = 0; ok && h < hops; h++) {
        ok = dsp_afe_bss_process(bss, (const dsp_spec_cplx_t *)bins, (const dsp_spec_cplx_t *)(bins + stride),
                                 (int16_t)angle[h], (uint8_t)confidence[h], searched[h] != 0.0f,
                                 (dsp_spec_cplx_t *)last, (dsp_spec_cplx_t *)(last + stride)) == ESP_OK;
        uint8_t seats = 0;
        uint8_t kept = 0;
        int16_t dirs[DIRECTIONS];
        int16_t number_deg[NUMBERS];
        dsp_afe_bss_state(bss, &seats, &got[offset_of(AT_GATE, hops) + h]);
        dsp_afe_bss_directions(bss, dirs);
        dsp_afe_bss_numbered(bss, &got[offset_of(AT_NUMBER_GATE, hops) + NUMBERS * h], number_deg);
        ok = ok && dsp_afe_bss_numbered_back(bss, back, &got[offset_of(AT_BACK_GATE, hops) + NUMBERS * h],
                                             &kept) == ESP_OK;
        got[offset_of(AT_SEATS, hops) + h] = (float)seats;
        got[offset_of(AT_BACK_KEPT, hops) + h] = (float)kept;
        for (size_t j = 0; j < DIRECTIONS; j++) {
            got[offset_of(AT_DIRS, hops) + DIRECTIONS * h + j] = (float)dirs[j];
        }
        for (size_t j = 0; j < NUMBERS; j++) {
            got[offset_of(AT_NUMBER_DEG, hops) + NUMBERS * h + j] = (float)number_deg[j];
        }
    }
    free(mem);
    return ok;
}

bool parity_bss(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t config, bins0, bins1, angle, confidence, searched, out0, out1, fields[AT_COUNT];
    bool ok = parity_tensor(buf, len, "config", &config) && parity_tensor(buf, len, "bins0", &bins0) &&
              parity_tensor(buf, len, "bins1", &bins1) && parity_tensor(buf, len, "angle", &angle) &&
              parity_tensor(buf, len, "confidence", &confidence) &&
              parity_tensor(buf, len, "searched", &searched) && parity_tensor(buf, len, "out0", &out0) &&
              parity_tensor(buf, len, "out1", &out1) && bins0.ndim == 3 && bins0.dims[0] == 1 &&
              bins0.dims[1] == GEN_GRID_N_BINS && bins0.dims[2] == FLOATS_PER_BIN && angle.ndim == 1;
    for (size_t f = 0; ok && f < AT_COUNT; f++) {
        ok = parity_tensor(buf, len, kNames[f], &fields[f]);
    }
    if (!ok) { return false; }
    const size_t hops = angle.dims[0];
    const size_t n = GEN_GRID_N_BINS * FLOATS_PER_BIN;
    const size_t per_hops = offset_of(AT_COUNT, hops);
    float cfg[CONFIG_VALUES];
    float *in = malloc((2 * n + 3 * hops) * sizeof(float));
    float *want = malloc((per_hops + 2 * n) * sizeof(float));
    float *got = malloc((per_hops + 2 * n) * sizeof(float));
    ok = in != NULL && want != NULL && got != NULL && parity_floats(&config, cfg, CONFIG_VALUES) &&
         parity_floats(&bins0, in, n) && parity_floats(&bins1, in + n, n) &&
         parity_floats(&angle, in + 2 * n, hops) && parity_floats(&confidence, in + 2 * n + hops, hops) &&
         parity_floats(&searched, in + 2 * n + 2 * hops, hops) && parity_floats(&out0, want + per_hops, n) &&
         parity_floats(&out1, want + per_hops + n, n);
    for (size_t f = 0; ok && f < AT_COUNT; f++) {
        ok = parity_floats(&fields[f], want + offset_of(f, hops), kWidth[f] * hops);
    }
    ok = ok &&
         run_hops(cfg, in, in + 2 * n, in + 2 * n + hops, in + 2 * n + 2 * hops, hops, got, got + per_hops);
    if (ok) {
        for (size_t f = 0; f < AT_COUNT; f++) {
            const size_t at = offset_of(f, hops);
            parity_report("bss", case_name, kNames[f], want + at, got + at, kWidth[f] * hops);
        }
        parity_report("bss", case_name, "out0", want + per_hops, got + per_hops, n);
        parity_report("bss", case_name, "out1", want + per_hops + n, got + per_hops + n, n);
    }
    free(got);
    free(want);
    free(in);
    return ok;
}
