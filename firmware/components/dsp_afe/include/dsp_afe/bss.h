/** Gate on doa's direction over the mean of both microphones: two seats split at broadside, or seats at
 * peaks of doa's delay density, the kept seat's share on one output, the rest on the other (KEHOACH 3.8).
 *  @ctx any | non-blocking | one instance per task
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "dsp_spec/fft.h"
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DSP_AFE_BSS_SEATS_MAX 4 // seats one separator holds

typedef enum {
    DSP_AFE_BSS_FIXED = 0, // two seats split at broadside
    DSP_AFE_BSS_LEARN = 1, // seats at the peaks of doa's delay density
} dsp_afe_bss_mode_t;

typedef enum {
    DSP_AFE_BSS_KEEP_ANGLE = 0, // the seat nearest keep_deg
    DSP_AFE_BSS_KEEP_MOST = 1,  // the seat that has had the most updates
} dsp_afe_bss_keep_t;

typedef struct {
    dsp_afe_bss_mode_t mode;
    dsp_afe_bss_keep_t keep_rule; // LEARN only; broadside keeps keep_deg's side
    float keep_deg;               // the kept talker, 0..180 as doa reads
    float gate_slope;             // per sample of delay at a boundary
    uint8_t conf_min;             // least doa confidence an update needs
    uint8_t seats_max;            // 2..DSP_AFE_BSS_SEATS_MAX
    float decision_s;             // speech whose updates make the density
    float recheck_s;              // between two searches for its peaks
    float first_speech_s;         // speech ahead of the first search
    uint8_t confirm;              // agreeing rechecks to take or move seats
    float occupancy_min;          // share of the window a peak's core holds
    float min_gap;                // samples of delay
    uint8_t bins_per_gap;         // density bins in one min_gap, 1..64
    uint8_t smooth_bins;          // kernel half-width, bins
    float prominence;             // share of a peak above its higher saddle
    float core_share;             // share of min_gap either side of a peak
    float seat_follow;            // share of the way a seat moves per recheck
    float boundary_share_min;     // share of the gap each seat keeps
    float boundary_follow;
    float seat_forget_s;
    uint8_t update_every_hops; // doa's search period
    float spacing_m;
    float speed_of_sound_m_s;
    float history_s; // held angles numbered_back reaches; 0 in the product
} dsp_afe_bss_config_t;

typedef struct dsp_afe_bss_s dsp_afe_bss_t;

/** Bytes this configuration needs, its ring of angles included; 0 when it is refused.
 *  @ctx any | non-blocking
 */
size_t dsp_afe_bss_workspace_bytes(const dsp_afe_bss_config_t *cfg);

/** Build the separator in mem: its delay and broadside gate tables, no seats, the gate at one half.
 *  @ctx task | non-blocking | caller owns mem
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE
 */
esp_err_t dsp_afe_bss_init(dsp_afe_bss_t **out, const dsp_afe_bss_config_t *cfg, void *mem, size_t bytes);

/** Count doa's angle when it searched this hop, learn, and split the mean of x0 and x1 into the kept seat's
 *  share y0 and the rest y1, GEN_GRID_N_BINS bins each.
 *  @ctx any | non-blocking
 *  @param angle_deg doa's angle, -1 while it has none; confidence its 0..255
 */
esp_err_t dsp_afe_bss_process(dsp_afe_bss_t *st, const dsp_spec_cplx_t *x0, const dsp_spec_cplx_t *x1,
                              int16_t angle_deg, uint8_t confidence, bool searched, dsp_spec_cplx_t *y0,
                              dsp_spec_cplx_t *y1);

/** Whole degrees of the kept seat and of the nearest other one; -1 without two seats.
 *  @ctx any | non-blocking | one candidate rule for choosing the talker's output (E8-T4)
 */
void dsp_afe_bss_directions(const dsp_afe_bss_t *st, int16_t angle_deg[2]);

/** Seats held now, 0 until a split; the gate of y0, 0..1.
 *  @ctx any | non-blocking
 */
void dsp_afe_bss_state(const dsp_afe_bss_t *st, uint8_t *seats, float *gate);

/** Gate and whole degrees of every seat number: a seat holds its number from the recheck that takes it
 * until it is forgotten; below two seats, 0 is the side past 90 deg and 1 the other (KEHOACH 3.8).
 *  @ctx any | non-blocking
 *  @param gate the gates the last search set, summing to 1; angle_deg -1 for a number no seat holds
 */
void dsp_afe_bss_numbered(const dsp_afe_bss_t *st, float gate[DSP_AFE_BSS_SEATS_MAX],
                          int16_t angle_deg[DSP_AFE_BSS_SEATS_MAX]);

/** Gate of every seat number for the angle held back_hops ago under the seats held now, and the kept seat's
 *  number: the delayed output's gates (KEHOACH 3.8); with no angle held yet, the gates it starts with.
 *  @ctx any | non-blocking
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG back_hops past the history_s the configuration keeps
 */
esp_err_t dsp_afe_bss_numbered_back(const dsp_afe_bss_t *st, uint32_t back_hops,
                                    float gate[DSP_AFE_BSS_SEATS_MAX], uint8_t *kept);

/** Forget every angle and seat and set the gate back to one half; the tables stay.
 *  @ctx any | non-blocking
 */
void dsp_afe_bss_reset(dsp_afe_bss_t *st);

#ifdef __cplusplus
}
#endif
