/** The weights and scratch the float32 steps of ns_float.h take, apart from those steps so that C++ may
 * include them: the dual-path block's sizes, a GRU's weights, the dual-path block's weights and its scratch
 * buffers.
 */
#pragma once

#include <stdint.h>

#define NS_FLOAT_F2 33 // bands the dual-path block runs over
#define NS_FLOAT_CH 16
#define NS_FLOAT_HALF 8
#define NS_FLOAT_PLANE (NS_FLOAT_F2 * NS_FLOAT_CH)
#define NS_FLOAT_NEWTON_STEPS 3

typedef struct {
    const float *wih; // 3 hidden rows of gates r, z, n by inputs
    const float *whh;
    const float *bih;
    const float *bhh;
} ns_float_gru_t;

typedef struct {
    ns_float_gru_t intra[2][2]; // [group][forward, backward]
    ns_float_gru_t inter[2];
    const float *intra_fc_w, *intra_fc_b, *intra_ln_w, *intra_ln_b;
    const float *inter_fc_w, *inter_fc_b, *inter_ln_w, *inter_ln_b;
} ns_float_dual_t;

typedef struct {
    float *t, *y, *u, *hn; // NS_FLOAT_PLANE each
    float *gi, *gh;        // 2 NS_FLOAT_F2 3 NS_FLOAT_HALF each
    float *rz;             // 2 NS_FLOAT_F2 2 NS_FLOAT_HALF
    float *nn;             // 2 NS_FLOAT_F2 NS_FLOAT_HALF
} ns_float_dual_work_t;
