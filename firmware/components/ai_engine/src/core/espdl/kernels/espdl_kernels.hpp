/** The kernels ai_engine's ops call, by names that carry no chip: espdl_kernels_impl.hpp of the build's
 *  implementation maps each onto its PIE or plain C symbol (KEHOACH 3.14, ADR-0045).
 *  @ctx any | non-blocking | caller owns every buffer
 */
#pragma once

#include <stdint.h>

#include "espdl_kernels_impl.hpp"

// A run's slot holds an address in 32 bits, as the ops store it on the chip.
#define AI_ENGINE_RUN_PTR(type, run, slot) ((type)(intptr_t)(run)[(slot)])

#ifdef __cplusplus
extern "C" {
#endif

void ai_engine_band_conv_s8(int8_t *out, const int8_t *const *columns, const int8_t *weights,
                            const int32_t *run);
void ai_engine_band_k1_linear_s8(int8_t *out, const int32_t *run);
void ai_engine_band_k1_res_s8(int8_t *out, const int32_t *run);
void ai_engine_band_k1_s8(int8_t *out, const int32_t *run);
void ai_engine_band_k3_l3_s8(int8_t *out, const int32_t *run);
void ai_engine_band_k3_res_s8(int8_t *out, const int32_t *run);
void ai_engine_band_k3_s8(int8_t *out, const int32_t *run);
void ai_engine_dw3x3_bias_row_s8(int8_t *out, const int8_t *const *rows, const int8_t *taps,
                                 const int32_t *run);
void ai_engine_dw3x3_row_s8(int8_t *out, const int8_t *const *rows, const int8_t *taps, const int32_t *run);
void ai_engine_dw_time_s8(int8_t *out, const int8_t *x, const int8_t *taps, const int32_t *run);
void ai_engine_dwk_row_s8(int8_t *out, const int8_t *const *rows, const int8_t *taps, const int32_t *run);
void ai_engine_pad_row_s8(int8_t *out, const int8_t *in, const int32_t *run);
void ai_engine_spread_row_s8(int8_t *out, const int8_t *in, int pixels);
void ai_engine_gn_outputs_s8(int8_t *y, const int8_t *x, const int8_t *tables, const int32_t *run);
void ai_engine_gn_squares_s8(float *squares, const int8_t *x, const float *deviations, const int32_t *run);
void ai_engine_gn_squares_x2_s8(float *squares, const int8_t *x, const float *deviations, const int32_t *run);
void ai_engine_gn_squares_x4_s8(float *squares, const int8_t *x, const float *deviations, const int32_t *run);
void ai_engine_gn_sums_s8(int32_t *sums, const int8_t *x, const int32_t *run);
void ai_engine_ln_deviations_s8(float *dev, float *variances, const int8_t *x, int pairs, const int32_t *run);
void ai_engine_ln_outputs_s8(int8_t *y, const float *dev, const float *scales, const float *gb, int pairs,
                             const int32_t *run);
void ai_engine_ln_scales_s8(float *scales, const float *variances, const int32_t *run, int pairs);
void ai_engine_lut_s16(int16_t *values, int n, const int16_t *middle, int shift);
void ai_engine_lut_s8(int8_t *values, int n, const int8_t *turned);
void ai_engine_pw_bias_row_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_conv_k12_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_conv_k24_n12_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_norm_c144_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_norm_c192_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_norm_c24_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_norm_c48_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_norm_c96_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_norm_row_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_residual_c144_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_residual_c192_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_residual_c24_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_residual_c48_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_residual_c96_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_residual_row_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_residual_x_c144_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_residual_x_c192_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_residual_x_c24_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_residual_x_c48_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_residual_x_c96_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_add_k128_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_add_k144_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_add_k192_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_add_k24_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_add_k48_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_add_k72_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_add_k96_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_add_rows_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_add_x_k128_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_add_x_k144_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_add_x_k192_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_add_x_k24_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_add_x_k48_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_add_x_k72_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_add_x_k96_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_add_x_rows_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_conv_k128_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_conv_k144_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_conv_k192_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_conv_k24_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_conv_k48_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_conv_k72_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_conv_k96_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_pw_conv_rows_s8(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);
void ai_engine_softmax_norm_s8(int8_t *y, const int8_t *x, const float *table, const float *scales, int rows,
                               const int32_t *strides);
void ai_engine_softmax_row_sums_s8(float *sums, const int8_t *x, const float *table, int len, int stride);
void ai_engine_softmax_sums_s8(float *sums, const int8_t *x, const float *table, int rows, int stride);
void ai_engine_stage_sum_row_s8(int8_t *row, const int8_t *x, const int8_t *weights, const int32_t *run);
void ai_engine_centred_squares_row_s8(int16_t *sums, const int8_t *x, const int8_t *mean, const int32_t *run);
void ai_engine_row_sums_s8(int16_t *sums, const int8_t *x, int groups);
void ai_engine_weighted_stats_row_s8(int16_t *sums, const int8_t *x, const int8_t *p, const int32_t *run);
void ai_engine_stem3_s8(int8_t *out, const int32_t *run);
void ai_engine_stem3_top_s8(int8_t *out, const int32_t *run);

#ifdef __cplusplus
}
#endif
