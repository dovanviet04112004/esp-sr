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
