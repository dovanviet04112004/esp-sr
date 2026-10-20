#include <stdlib.h>

#include "dsp_afe/bss.h"
#include "gen_grid.h"
#include "parity.h"

#define FLOATS_PER_BIN 2
#define CONFIG_VALUES 23 // in the order of srpipe.dsp.emit_golden.BSS_FIELDS
#define DIRECTIONS 2
#define NUMBERS DSP_AFE_BSS_SEATS_MAX

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
                                  .kmeans_rounds = (uint8_t)v[9],
                                  .confirm = (uint8_t)v[10],
                                  .occupancy_min = v[11],
                                  .valley_max = v[12],
                                  .valley_width = v[13],
                                  .min_gap = v[14],
                                  .boundary_share_min = v[15],
                                  .boundary_follow = v[16],
                                  .track_tau_s = v[17],
                                  .track_gate = v[18],
                                  .seat_forget_s = v[19],
                                  .update_every_hops = (uint8_t)v[20],
                                  .spacing_m = v[21],
                                  .speed_of_sound_m_s = v[22]};
}

// got holds the gate, seats, directions and every number's gate and degrees of each hop; last the last
// outputs.
static bool run_hops(const float *config, const float *bins, const float *angle, const float *confidence,
                     const float *searched, size_t hops, float *got, float *last)
{
    const dsp_afe_bss_config_t cfg = config_of(config);
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
        int16_t dirs[DIRECTIONS];
        int16_t number_deg[NUMBERS];
        dsp_afe_bss_state(bss, &seats, &got[h]);
        dsp_afe_bss_directions(bss, dirs);
        dsp_afe_bss_numbered(bss, &got[(2 + DIRECTIONS) * hops + NUMBERS * h], number_deg);
        got[hops + h] = (float)seats;
        got[2 * hops + DIRECTIONS * h] = (float)dirs[0];
        got[2 * hops + DIRECTIONS * h + 1] = (float)dirs[1];
        for (size_t j = 0; j < NUMBERS; j++) {
            got[(2 + DIRECTIONS + NUMBERS) * hops + NUMBERS * h + j] = (float)number_deg[j];
        }
    }
    free(mem);
    return ok;
}

bool parity_bss(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t config, bins0, bins1, angle, confidence, searched, gate, seats, directions, number_gate,
        number_deg, out0, out1;
    if (!parity_tensor(buf, len, "config", &config) || !parity_tensor(buf, len, "bins0", &bins0) ||
        !parity_tensor(buf, len, "bins1", &bins1) || !parity_tensor(buf, len, "angle", &angle) ||
        !parity_tensor(buf, len, "confidence", &confidence) ||
        !parity_tensor(buf, len, "searched", &searched) || !parity_tensor(buf, len, "gate", &gate) ||
        !parity_tensor(buf, len, "seats", &seats) || !parity_tensor(buf, len, "directions", &directions) ||
        !parity_tensor(buf, len, "number_gate", &number_gate) ||
        !parity_tensor(buf, len, "number_deg", &number_deg) || !parity_tensor(buf, len, "out0", &out0) ||
        !parity_tensor(buf, len, "out1", &out1) || bins0.ndim != 3 || bins0.dims[0] != 1 ||
        bins0.dims[1] != GEN_GRID_N_BINS || bins0.dims[2] != FLOATS_PER_BIN || angle.ndim != 1) {
        return false;
    }
    const size_t hops = angle.dims[0];
    const size_t n = GEN_GRID_N_BINS * FLOATS_PER_BIN;
    const size_t per_hop = 2 + DIRECTIONS + 2 * NUMBERS;
    const size_t gates_at = (2 + DIRECTIONS) * hops;
    const size_t degs_at = (2 + DIRECTIONS + NUMBERS) * hops;
    float cfg[CONFIG_VALUES];
    float *in = malloc((2 * n + 3 * hops) * sizeof(float));
    float *want = malloc((per_hop * hops + 2 * n) * sizeof(float));
    float *got = malloc((per_hop * hops + 2 * n) * sizeof(float));
    const bool ok = in != NULL && want != NULL && got != NULL && parity_floats(&config, cfg, CONFIG_VALUES) &&
                    parity_floats(&bins0, in, n) && parity_floats(&bins1, in + n, n) &&
                    parity_floats(&angle, in + 2 * n, hops) &&
                    parity_floats(&confidence, in + 2 * n + hops, hops) &&
                    parity_floats(&searched, in + 2 * n + 2 * hops, hops) &&
                    parity_floats(&gate, want, hops) && parity_floats(&seats, want + hops, hops) &&
                    parity_floats(&directions, want + 2 * hops, DIRECTIONS * hops) &&
                    parity_floats(&number_gate, want + gates_at, NUMBERS * hops) &&
                    parity_floats(&number_deg, want + degs_at, NUMBERS * hops) &&
                    parity_floats(&out0, want + per_hop * hops, n) &&
                    parity_floats(&out1, want + per_hop * hops + n, n) &&
                    run_hops(cfg, in, in + 2 * n, in + 2 * n + hops, in + 2 * n + 2 * hops, hops, got,
                             got + per_hop * hops);
    if (ok) {
        parity_report("bss", case_name, "gate", want, got, hops);
        parity_report("bss", case_name, "seats", want + hops, got + hops, hops);
        parity_report("bss", case_name, "directions", want + 2 * hops, got + 2 * hops, DIRECTIONS * hops);
        parity_report("bss", case_name, "number_gate", want + gates_at, got + gates_at, NUMBERS * hops);
        parity_report("bss", case_name, "number_deg", want + degs_at, got + degs_at, NUMBERS * hops);
        parity_report("bss", case_name, "out0", want + per_hop * hops, got + per_hop * hops, n);
        parity_report("bss", case_name, "out1", want + per_hop * hops + n, got + per_hop * hops + n, n);
    }
    free(got);
    free(want);
    free(in);
    return ok;
}
