// Convolutions along the bands of BandBlock (KEHOACH 3.14 step 2.5), each output band's 16 channels as esp-dl
// 3.3.11's aligned int8 kernels sum them: bias loaded into QACC in esp-dl's layout, the taps within the map, one
// product a real input channel, round half up as tie728_s8_vector_round_result does. Pointers 16-byte aligned.
//
// void ai_engine_band_conv_s8_esp32s3(int8_t *out, const int8_t *const *columns, const int8_t *weights,
//                                     const int32_t *run)
// columns: a time tap's column at its band 0, nullptr for one past the frames. weights [time tap][band tap][12][16],
// read 32 bytes past a column's last tap. run: bands, band table (per band: the first tap's input offset, its
// weight offset, taps), time taps, weight bytes a time tap, input step between band taps, output band stride, mac
// shift (1 or more), bias, residual (0 none, else its shift onto the sum's exponent plus 1), residual less output
// address, ReLU (1) or not (0). With a residual, x as esp-dl's int8 RequantizeLinear rounds it is added saturating
// as its Add does; then max(0, .) with ReLU. out: one column, band 0 first.

#include "pie_s8_esp32s3.inc"

    .text
    .align  4
    .global ai_engine_band_conv_s8_esp32s3
    .type   ai_engine_band_conv_s8_esp32s3, @function

// a2 out, a3 columns, a4 weights, a5 run, a6 bands left, a7 band table, a8 weight rows, a9 inputs, a10 input step,
// a12 taps, a13 mac shift less 1, a14 time tap, a15 one.
ai_engine_band_conv_s8_esp32s3:
    entry   a1, 32
    ones_in_q7 a15
    l32i    a6, a5, 0
    l32i    a7, a5, 4
    l32i    a13, a5, 24
    addi    a13, a13, -1
    beqz    a6, 9f
1:
    l32i    a9, a5, 28
    bias_into_qacc  a9
    l32i    a10, a5, 16
    movi.n  a14, 0
2:
    addx4   a11, a14, a3
    l32i    a9, a11, 0
    beqz    a9, 4f
    l32i    a11, a7, 0
    add     a9, a9, a11
    l32i    a11, a5, 12
    mull    a8, a11, a14
    add     a8, a8, a4
    l32i    a11, a7, 4
    add     a8, a8, a11
    ee.vld.128.ip   q1, a8, 16
    ee.vld.128.ip   q2, a8, 16
    l32i    a12, a7, 8
    loopgtz a12, 4f
    ee.vld.128.xp   q0, a9, a10
    ee.vsmulas.s8.qacc.ld.incp  q1, a8, q1, q0, 0
    ee.vsmulas.s8.qacc.ld.incp  q2, a8, q2, q0, 1
    ee.vsmulas.s8.qacc.ld.incp  q1, a8, q1, q0, 2
    ee.vsmulas.s8.qacc.ld.incp  q2, a8, q2, q0, 3
    ee.vsmulas.s8.qacc.ld.incp  q1, a8, q1, q0, 4
    ee.vsmulas.s8.qacc.ld.incp  q2, a8, q2, q0, 5
    ee.vsmulas.s8.qacc.ld.incp  q1, a8, q1, q0, 6
    ee.vsmulas.s8.qacc.ld.incp  q2, a8, q2, q0, 7
    ee.vsmulas.s8.qacc.ld.incp  q1, a8, q1, q0, 8
    ee.vsmulas.s8.qacc.ld.incp  q2, a8, q2, q0, 9
    ee.vsmulas.s8.qacc.ld.incp  q1, a8, q1, q0, 10
    ee.vsmulas.s8.qacc.ld.incp  q2, a8, q2, q0, 11
4:
    addi    a14, a14, 1
    l32i    a11, a5, 8
    blt     a14, a11, 2b
    movi.n  a15, 1
    round_into  q3, a13, a15
    l32i    a11, a5, 32
    beqz    a11, 6f
    l32i    a10, a5, 36
    add     a10, a10, a2
    ee.vld.128.ip   q4, a10, 0
    addi    a11, a11, -2
    bltz    a11, 5f
    ee.zero.qacc
    ee.vmulas.s8.qacc   q4, q7
    round_into  q4, a11, a15
5:
    ee.vadds.s8     q3, q3, q4
6:
    l32i    a11, a5, 40
    beqz    a11, 7f
    movi.n  a15, 0
    ee.vrelu.s8     q3, a15, a15
7:
    l32i    a11, a5, 20
    ee.vst.128.xp   q3, a2, a11
    addi    a7, a7, 12
    addi    a6, a6, -1
    bnez    a6, 1b
9:
    retw.n

// Fast kernels for a column whose every time tap lies within the frames, the same sums as above.
// void ai_engine_band_<variant>_s8_esp32s3(int8_t *out, const int32_t *run)
// run: bands, band table, input step between band taps, output band stride, mac shift less 1, bias, residual shift
// less 1 (-1: added as it is), residual less output address, then the columns of the time taps, then each time
// tap's weights [band tap][lanes][16]. Variants: k1 and k3 the time taps, res a residual, linear no ReLU; 12 lanes
// unless l3, three.

// QACC += one input pixel's lanes in \in times their weight rows, streaming two steps ahead through q1 and q2 from
// a8; \first 1 when q2 holds the pixel's first row.
.macro lane_products in, lanes, first
    .irp lane, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15
    .if \lane < \lanes
    .if ((\lane + \first) % 2) == 0
    ee.vsmulas.s8.qacc.ld.incp  q1, a8, q1, \in, \lane
    .else
    ee.vsmulas.s8.qacc.ld.incp  q2, a8, q2, \in, \lane
    .endif
    .endif
    .endr
.endm

// The products of a12 taps (1 or more) from a9, a10 apart, each input loaded a tap ahead in q0 or q5, never past
// the last tap. a14 is scratch.
.macro taps_of lanes
    ee.vld.128.ip   q1, a8, 16
    ee.vld.128.ip   q2, a8, 16
    ee.vld.128.xp   q0, a9, a10
    addi    a14, a12, -1
    srli    a14, a14, 1
    loopgtz a14, 1f
    ee.vld.128.xp   q5, a9, a10
    lane_products q0, \lanes, 0
    ee.vld.128.xp   q0, a9, a10
    lane_products q5, \lanes, (\lanes % 2)
1:
    bbsi    a12, 0, 2f
    ee.vld.128.xp   q5, a9, a10
    lane_products q0, \lanes, 0
    lane_products q5, \lanes, (\lanes % 2)
    j       3f
2:
    lane_products q0, \lanes, 0
3:
.endm

// a2 out, a5 run (passed in a3), a6 bands left, a7 band table, a8 weight rows, a9 inputs, a10 input step, a11
// input offset, a12 taps, a13 mac shift less 1.
.macro band_fast name, kt, lanes, residual, relu
    .text
    .align  4
    .global \name
    .type   \name, @function
\name:
    entry   a1, 32
    mov     a5, a3
    ones_in_q7 a15
    l32i    a6, a5, 0
    l32i    a7, a5, 4
    l32i    a10, a5, 8
    l32i    a13, a5, 16
    beqz    a6, 9f
7:
    l32i    a9, a5, 20
    bias_into_qacc  a9
    l32i    a11, a7, 0
    l32i    a12, a7, 8
    .irp col, 0, 1, 2
    .if \col < \kt
    l32i    a9, a5, (32 + 4 * \col)
    add     a9, a9, a11
    l32i    a8, a5, (44 + 4 * \col)
    l32i    a15, a7, 4
    add     a8, a8, a15
    taps_of \lanes
    .endif
    .endr
    movi.n  a15, 1
    round_into  q3, a13, a15
    .if \residual
    l32i    a11, a5, 28
    add     a11, a11, a2
    ee.vld.128.ip   q4, a11, 0
    l32i    a11, a5, 24
    bltz    a11, 8f
    ee.zero.qacc
    ee.vmulas.s8.qacc   q4, q7
    round_into  q4, a11, a15
8:
    ee.vadds.s8     q3, q3, q4
    .endif
    .if \relu
    movi.n  a15, 0
    ee.vrelu.s8     q3, a15, a15
    .endif
    l32i    a11, a5, 12
    ee.vst.128.xp   q3, a2, a11
    addi    a7, a7, 12
    addi    a6, a6, -1
    bnez    a6, 7b
9:
    retw.n
.endm

    band_fast ai_engine_band_k1_s8_esp32s3, 1, 12, 0, 1
    band_fast ai_engine_band_k1_res_s8_esp32s3, 1, 12, 1, 1
    band_fast ai_engine_band_k1_linear_s8_esp32s3, 1, 12, 0, 0
    band_fast ai_engine_band_k3_s8_esp32s3, 3, 12, 0, 1
    band_fast ai_engine_band_k3_res_s8_esp32s3, 3, 12, 1, 1
    band_fast ai_engine_band_k3_l3_s8_esp32s3, 3, 3, 0, 1
