/** Audio front end: interleaved microphone hops in, one clean channel plus its figures out.
 *  Chain and order: KEHOACH 3.2. Contract frozen at tag contract-v1 (KEHOACH 4.5.5).
 *  @ctx task | non-blocking | feed and fetch of one instance come from the same task
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "dsp_afe/ns.h"
#include "dsp_spec/fft.h"
#include "esp_err.h"
#include "gen_grid.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DSP_AFE_FIFO_FRAMES 4

typedef enum {
    DSP_AFE_SPATIAL_NONE = 0, // mean of the two channels
    DSP_AFE_SPATIAL_GSC,
    DSP_AFE_SPATIAL_BSS,
} dsp_afe_spatial_t;

typedef enum {
    DSP_AFE_FLAG_GAP = 1u << 0,     // upstream dropped frames
    DSP_AFE_FLAG_CLIPPED = 1u << 1, // an input sample hit full scale
    DSP_AFE_FLAG_AEC_DIVERGED = 1u << 2,
    DSP_AFE_FLAG_NO_REF = 1u << 3, // "MMR" asked, reference is silent
} dsp_afe_flag_t;

typedef struct {
    dsp_spec_cplx_t balance[GEN_GRID_N_BINS]; // ch1 gain per bin, NVS calib/bal
    uint32_t aec_delay_samples;               // NVS calib/aec_delay
} dsp_afe_calib_t;

typedef struct {
    const char *input_format; // "MM", or "MMR" with a reference
    dsp_afe_spatial_t spatial;
    const dsp_afe_ns_ops_t *ns; // NULL: OM-LSA floor if built, else no ns
    void *ns_ctx;
    const dsp_afe_calib_t *calib; // NULL skips balance, zero delay
    float ns_floor_db;
    float agc_target_dbfs;
    uint8_t vad_aggressiveness;
} dsp_afe_config_t;

typedef struct {
    int16_t pcm[GEN_GRID_HOP_SAMPLES]; // one clean channel
    uint32_t seq;                      // hop count since init
    int16_t doa_deg;                   // 0..180, -1 when unknown
    uint8_t doa_conf;
    uint8_t vad;       // 0 or 1
    int8_t level_dbfs; // measured ahead of agc
    int8_t gain_db;    // agc gain on this hop
    uint16_t flags;    // dsp_afe_flag_t bits
} dsp_afe_frame_t;

typedef struct {
    uint32_t hops_in;
    uint32_t hops_out;
    uint32_t fifo_overflows;
    uint32_t gaps;
    uint32_t clipped_hops;
} dsp_afe_stats_t;

typedef enum {
    DSP_AFE_PARAM_NS_FLOOR_DB = 0,
    DSP_AFE_PARAM_AGC_TARGET_DBFS,
    DSP_AFE_PARAM_VAD_AGGRESSIVENESS,
} dsp_afe_param_t;

typedef struct dsp_afe_s dsp_afe_t;

/** Split the memory this configuration needs into hot (touched every hop) and cold parts.
 *  @ctx any | non-blocking
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG unknown format or parameter out of range
 *       | ESP_ERR_NOT_SUPPORTED "MMR" without aec, or a spatial path not built (Kconfig)
 */
esp_err_t dsp_afe_workspace_bytes(const dsp_afe_config_t *cfg, size_t *hot_bytes, size_t *cold_bytes);

/** Build the chain; hot should sit in internal RAM, cold may sit in PSRAM (KEHOACH 6.5).
 *  @ctx task | non-blocking | caller owns both regions for the life of afe; cfg is copied
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE a region is short
 */
esp_err_t dsp_afe_init(dsp_afe_t **out, const dsp_afe_config_t *cfg, void *hot, size_t hot_bytes, void *cold,
                       size_t cold_bytes);

/** Run frames hops of interleaved samples, GEN_GRID_HOP_SAMPLES per channel each, through the chain.
 *  @ctx task | non-blocking | same task as dsp_afe_fetch
 *  @ret ESP_OK | ESP_ERR_NO_MEM the inner fifo of DSP_AFE_FIFO_FRAMES is full, nothing consumed
 */
esp_err_t dsp_afe_feed(dsp_afe_t *afe, const int16_t *interleaved, size_t frames);

/** Take the oldest clean hop.
 *  @ctx task | non-blocking | same task as dsp_afe_feed
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND nothing ready
 */
esp_err_t dsp_afe_fetch(dsp_afe_t *afe, dsp_afe_frame_t *out);

/** Drop buffered hops and adaptive state after a gap; the next frame carries DSP_AFE_FLAG_GAP.
 *  Every module returns to the state its init leaves and keeps the tables init built; seq runs on, so only
 *  doa's every-second-hop search may keep another phase than a freshly built chain (KEHOACH 4.5.5).
 *  @ctx task | non-blocking
 */
void dsp_afe_reset(dsp_afe_t *afe);

/** After a short gap, drop buffered hops and the samples older than the gap, keeping every estimate
 *  the modules have learnt; the next frame carries DSP_AFE_FLAG_GAP (KEHOACH 4.5.5).
 *  @ctx task | non-blocking
 */
void dsp_afe_resume(dsp_afe_t *afe);

/** Change one runtime parameter, as SET_CONFIG asks.
 *  @ctx task | non-blocking | same task as dsp_afe_feed
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG value out of range
 */
esp_err_t dsp_afe_set_param(dsp_afe_t *afe, dsp_afe_param_t param, float value);

/** Copy the counters.
 *  @ctx any | non-blocking | a torn read across tasks is possible and harmless
 */
void dsp_afe_stats(const dsp_afe_t *afe, dsp_afe_stats_t *out);

#ifdef __cplusplus
}
#endif
