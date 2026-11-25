/** UL-UNAS's streaming net in float32 C for the ns slot (KEHOACH 3.9, ADR-0048): one hop of complex bins in,
 * per-bin gains out, every step as srpipe.tasks.ns.ulunas_mirror takes it so the two match bit for bit. The
 * weights are a blob of float32 and int32 that the mirror's pack writes; the state holds the caches and every
 * scratch buffer.
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "ns_float/ns_float_types.h"

#ifdef __cplusplus
extern "C" {
#endif

#define AI_ENGINE_ULUNAS_BINS 257
#define AI_ENGINE_ULUNAS_LOW 65 // bins kept as they are below the ERB bands
#define AI_ENGINE_ULUNAS_ERB 129
#define AI_ENGINE_ULUNAS_UNITS 5    // blocks of the encoder, and of the decoder
#define AI_ENGINE_ULUNAS_CACHE 5358 // the six time caches, one after another
#define AI_ENGINE_ULUNAS_TFA 402    // the ten attention GRUs' hidden units
#define AI_ENGINE_ULUNAS_SKIPS 3948 // the encoder's five outputs, one after another
#define AI_ENGINE_ULUNAS_PLANE 1560 // the widest block plane: 24 channels by 65 bins
#define AI_ENGINE_ULUNAS_SPAN 3216  // the widest span with padding: 24 by 2 frames by 67
#define AI_ENGINE_ULUNAS_CHUNKS 33  // frequency GRU steps over 129 bins, 4 a step

typedef struct {
    const float *w;
    const float *b;
    const float *act_a; // AffinePReLU's scale, (channels, width); NULL without one
    const float *act_b;
    const float *act_slope;
} ai_engine_ulunas_layer_t;

typedef struct {
    ns_float_gru_t gru; // over time, 2 C hidden units
    const float *fc_w, *fc_b;
    ns_float_gru_t fa[2]; // over frequency chunks, forward then backward
    const float *fa_fc_w, *fa_fc_b;
} ai_engine_ulunas_tfa_t;

typedef struct {
    ai_engine_ulunas_layer_t layer[3]; // XConv: conv; XDWS: point, depthwise; XMB: and point
    ai_engine_ulunas_tfa_t tfa;
} ai_engine_ulunas_unit_t;

typedef struct {
    const int32_t *erb_ptr, *erb_idx;
    const float *erb_coef;
    const int32_t *ierb_ptr, *ierb_idx;
    const float *ierb_coef;
    float scale; // Hann's sum over the slot window's
    ai_engine_ulunas_unit_t enc[AI_ENGINE_ULUNAS_UNITS];
    ns_float_dual_t dual[2];
    ai_engine_ulunas_unit_t dec[AI_ENGINE_ULUNAS_UNITS];
} ai_engine_ulunas_weights_t;

typedef struct {
    float cache[AI_ENGINE_ULUNAS_CACHE];
    float tfa[AI_ENGINE_ULUNAS_TFA];
    float inter[2][NS_FLOAT_PLANE];
    float feat[AI_ENGINE_ULUNAS_BINS];
    float erb[AI_ENGINE_ULUNAS_ERB];
    float skip[AI_ENGINE_ULUNAS_SKIPS];
    float in[AI_ENGINE_ULUNAS_PLANE];
    float y[AI_ENGINE_ULUNAS_PLANE];
    float p[AI_ENGINE_ULUNAS_PLANE];
    float q[AI_ENGINE_ULUNAS_PLANE];
    float d[AI_ENGINE_ULUNAS_PLANE];
    float span[AI_ENGINE_ULUNAS_SPAN];
    float t[NS_FLOAT_PLANE];
    float u[NS_FLOAT_PLANE];
    float hn[NS_FLOAT_PLANE];
    float dy[NS_FLOAT_PLANE];
    float gi[2 * NS_FLOAT_F2 * 3 * NS_FLOAT_HALF];
    float gh[2 * NS_FLOAT_F2 * 3 * NS_FLOAT_HALF];
    float rz[2 * NS_FLOAT_F2 * 2 * NS_FLOAT_HALF];
    float nn[2 * NS_FLOAT_F2 * NS_FLOAT_HALF];
    float zf[4 * AI_ENGINE_ULUNAS_CHUNKS];
    float both[8 * AI_ENGINE_ULUNAS_CHUNKS];
    float af[4 * AI_ENGINE_ULUNAS_CHUNKS];
    float m[AI_ENGINE_ULUNAS_ERB];
    float mask[AI_ENGINE_ULUNAS_BINS];
} ai_engine_ulunas_state_t;

/** Point w into a blob the mirror's pack wrote, checking its magic, version and length.
 *  @ctx any | non-blocking | w points into blob, which must outlive it
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE | ESP_ERR_INVALID_VERSION
 */
esp_err_t ai_engine_ulunas_weights(const void *blob, size_t bytes, ai_engine_ulunas_weights_t *w);

/** Zero the caches and recurrent states, as the stream's first hop expects.
 *  @ctx any | non-blocking
 */
void ai_engine_ulunas_reset(ai_engine_ulunas_state_t *st);

/** One hop: bins are AI_ENGINE_ULUNAS_BINS complex values, re then im each; gain gets the mask at most 1.
 *  @ctx any | non-blocking | caller owns st, sized as the struct, and calls hops in stream order
 */
void ai_engine_ulunas_step(const ai_engine_ulunas_weights_t *w, ai_engine_ulunas_state_t *st,
                           const float *bins, float *gain);

#ifdef __cplusplus
}
#endif
