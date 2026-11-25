/** GTCRN's streaming net in float32 C for the ns slot (KEHOACH 3.9, ADR-0047): one hop of complex bins in,
 * per-bin gains out, every step as srpipe.tasks.ns.gtcrn_mirror takes it so the two match bit for bit. The
 * weights are a blob of float32 and int32 that the mirror's pack writes; the state holds the caches and every
 * scratch buffer.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define AI_ENGINE_GTCRN_BINS 257
#define AI_ENGINE_GTCRN_LOW 65 // bins kept as they are below the ERB bands
#define AI_ENGINE_GTCRN_ERB 129
#define AI_ENGINE_GTCRN_F1 65
#define AI_ENGINE_GTCRN_F2 33
#define AI_ENGINE_GTCRN_CH 16
#define AI_ENGINE_GTCRN_HALF 8
#define AI_ENGINE_GTCRN_BLOCKS 3

typedef struct {
    const float *w;
    const float *b;
    float slope; // PReLU's one slope; unused by tanh and plain layers
} ai_engine_gtcrn_conv_t;

typedef struct {
    const float *wih; // 3 hidden rows of gates r, z, n by inputs
    const float *whh;
    const float *bih;
    const float *bhh;
} ai_engine_gtcrn_gru_t;

typedef struct {
    ai_engine_gtcrn_conv_t pc1;
    ai_engine_gtcrn_conv_t dw;
    ai_engine_gtcrn_conv_t pc2;
    ai_engine_gtcrn_gru_t att;
    const float *att_w;
    const float *att_b;
    int dil;
} ai_engine_gtcrn_block_t;

typedef struct {
    ai_engine_gtcrn_gru_t intra[2][2]; // [group][forward, backward]
    ai_engine_gtcrn_gru_t inter[2];
    const float *intra_fc_w, *intra_fc_b, *intra_ln_w, *intra_ln_b;
    const float *inter_fc_w, *inter_fc_b, *inter_ln_w, *inter_ln_b;
} ai_engine_gtcrn_dual_t;

typedef struct {
    const int32_t *erb_ptr, *erb_idx;
    const float *erb_coef;
    const int32_t *ierb_ptr, *ierb_idx;
    const float *ierb_coef;
    ai_engine_gtcrn_conv_t en0, en1;
    ai_engine_gtcrn_block_t enc[AI_ENGINE_GTCRN_BLOCKS];
    ai_engine_gtcrn_dual_t dual[2];
    ai_engine_gtcrn_block_t dec[AI_ENGINE_GTCRN_BLOCKS];
    ai_engine_gtcrn_conv_t de3, de4;
} ai_engine_gtcrn_weights_t;

#define AI_ENGINE_GTCRN_SPAN_MAX 11 // a block's cached frames and this hop's, at dilation 5
#define AI_ENGINE_GTCRN_PLANE (AI_ENGINE_GTCRN_F2 * AI_ENGINE_GTCRN_CH)

typedef struct {
    float cache[2 * AI_ENGINE_GTCRN_BLOCKS]
               [AI_ENGINE_GTCRN_CH * AI_ENGINE_GTCRN_SPAN_MAX * AI_ENGINE_GTCRN_F2];
    float tra[2 * AI_ENGINE_GTCRN_BLOCKS][2 * AI_ENGINE_GTCRN_HALF];
    float inter[2][AI_ENGINE_GTCRN_PLANE];
    float x[3 * AI_ENGINE_GTCRN_BINS];
    float e[3 * AI_ENGINE_GTCRN_ERB];
    float s[9 * AI_ENGINE_GTCRN_ERB];
    float o0[AI_ENGINE_GTCRN_CH * AI_ENGINE_GTCRN_F1];
    float o1[AI_ENGINE_GTCRN_PLANE];
    float skip[AI_ENGINE_GTCRN_BLOCKS][AI_ENGINE_GTCRN_PLANE];
    float y[AI_ENGINE_GTCRN_PLANE];
    float in[AI_ENGINE_GTCRN_PLANE];
    float h[AI_ENGINE_GTCRN_PLANE];
    float d[AI_ENGINE_GTCRN_PLANE];
    float t[AI_ENGINE_GTCRN_PLANE];
    float u[AI_ENGINE_GTCRN_PLANE];
    float hn[AI_ENGINE_GTCRN_PLANE];
    float m1[AI_ENGINE_GTCRN_CH * AI_ENGINE_GTCRN_F1];
    float m2[2 * AI_ENGINE_GTCRN_ERB];
    float mask[2 * AI_ENGINE_GTCRN_BINS];
} ai_engine_gtcrn_state_t;

/** Point w into a blob the mirror's pack wrote, checking its magic, version and length.
 *  @ctx any | non-blocking | w points into blob, which must outlive it
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE | ESP_ERR_INVALID_VERSION
 */
esp_err_t ai_engine_gtcrn_weights(const void *blob, size_t bytes, ai_engine_gtcrn_weights_t *w);

/** Zero the caches and recurrent states, as the stream's first hop expects.
 *  @ctx any | non-blocking
 */
void ai_engine_gtcrn_reset(ai_engine_gtcrn_state_t *st);

/** One hop: bins are AI_ENGINE_GTCRN_BINS complex values, re then im each; gain gets the mask's magnitude at
 * most 1.
 *  @ctx any | non-blocking | caller owns st, sized as the struct, and calls hops in stream order
 */
void ai_engine_gtcrn_step(const ai_engine_gtcrn_weights_t *w, ai_engine_gtcrn_state_t *st, const float *bins,
                          float *gain);

#ifdef __cplusplus
}
#endif
