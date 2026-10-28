// Kernels of GroupNormS8 (KEHOACH 3.14 step 2.5) on a [T, C] int8 map whose groups are W consecutive channels, run on
// a batch of adjacent groups at a time. Row stride in bytes; W a multiple of 8; pointers 16-byte aligned unless said.
//
// void ai_engine_gn_sums_s8_esp32s3(int32_t *sums, const int8_t *x, const int32_t *run)
// run: frames, row stride, W, groups. x: the batch's first channel, 8-byte aligned. sums[k]: group k's exact int8 sum,
// 16 values a step into ACCX, the last 8 of a group through a mask of 8 ones.
//
// void ai_engine_gn_squares_s8_esp32s3(float *squares, const int8_t *x, const float *deviations, const int32_t *run)
// run: frames, row stride, W. Two groups; deviations: [2][256], entry u of group k the int8 value of byte u less the
// group's mean, rounded once. squares[k] sums group k's deviations squared channel after channel then frame after
// frame, each step one fused multiply-add, the two chains an instruction apart. The _x2 and _x4 kernels take each
// frame 2 or 4 times in a row, as over its rows repeated by a nearest Resize.
//
// void ai_engine_gn_outputs_s8_esp32s3(int8_t *y, const int8_t *x, const int8_t *tables, const int32_t *run)
// run: frames, columns, W, groups, mul shift less 1, the Mul's weights and the Add's bias at the batch's first
// channel, a buffer of columns bytes, row stride. tables: [groups][256], entry u of a group the int8 norm of the int8
// value of byte u. Each frame's norms are looked up into the buffer, 4 a step, then times the weights through QACC
// rounded half up as tie728 does, the bias added saturating, 16 channels at a time. columns a multiple of 16.

#include "pie_s8_esp32s3.inc"

    .text
    .global ai_engine_gn_sums_s8_esp32s3
    .type   ai_engine_gn_sums_s8_esp32s3, @function
    .global ai_engine_gn_squares_s8_esp32s3
    .type   ai_engine_gn_squares_s8_esp32s3, @function
    .global ai_engine_gn_squares_x2_s8_esp32s3
    .type   ai_engine_gn_squares_x2_s8_esp32s3, @function
    .global ai_engine_gn_squares_x4_s8_esp32s3
    .type   ai_engine_gn_squares_x4_s8_esp32s3, @function
    .global ai_engine_gn_outputs_s8_esp32s3
    .type   ai_engine_gn_outputs_s8_esp32s3, @function

    .align  4
ai_engine_gn_sums_s8_esp32s3:
    entry   a1, 32
    ones_in_q7 a6
    movi    a6, 257
    slli    a7, a6, 16
    or      a6, a6, a7
    ee.zero.q       q6
    ee.movi.32.q    q6, a6, 0
    ee.movi.32.q    q6, a6, 1
    l32i    a6, a4, 0
    l32i    a7, a4, 4
    l32i    a8, a4, 8
    l32i    a9, a4, 12
    srli    a10, a8, 4
    extui   a11, a8, 3, 1
1:
    ee.zero.accx
    mov     a12, a3
    mov     a13, a6
2:
    mov     a14, a12
    loopgtz a10, 3f
    ee.vld.l.64.ip  q0, a14, 8
    ee.vld.h.64.ip  q0, a14, 8
    ee.vmulas.s8.accx   q0, q7
3:
    beqz    a11, 4f
    ee.vld.l.64.ip  q0, a14, 8
    ee.vmulas.s8.accx   q0, q6
4:
    add     a12, a12, a7
    addi    a13, a13, -1
    bnez    a13, 2b
    rur.accx_0  a14
    s32i    a14, a2, 0
    addi    a2, a2, 4
    add     a3, a3, a8
    addi    a9, a9, -1
    bnez    a9, 1b
    retw.n

// Each frame's deviations multiplied in repeat times.
.macro gn_squares repeat
    entry   a1, 32
    l32i    a6, a5, 0
    l32i    a7, a5, 4
    l32i    a8, a5, 8
    mov     a9, a8
    addmi   a5, a4, 1024
    const.s f12, 0
    const.s f13, 0
1:
    mov     a10, a3
    add     a11, a3, a9
    loopgtz a6, 2f
    l8ui    a12, a10, 0
    l8ui    a13, a11, 0
    add     a10, a10, a7
    addx4   a12, a12, a4
    addx4   a13, a13, a5
    add     a11, a11, a7
    lsi     f0, a12, 0
    lsi     f1, a13, 0
    .rept \repeat
    madd.s  f12, f0, f0
    madd.s  f13, f1, f1
    .endr
2:
    addi    a3, a3, 1
    addi    a8, a8, -1
    bnez    a8, 1b
    ssi     f12, a2, 0
    ssi     f13, a2, 4
    retw.n
.endm

    .align  4
ai_engine_gn_squares_s8_esp32s3:
    gn_squares 1

    .align  4
ai_engine_gn_squares_x2_s8_esp32s3:
    gn_squares 2

    .align  4
ai_engine_gn_squares_x4_s8_esp32s3:
    gn_squares 4

    .align  4
ai_engine_gn_outputs_s8_esp32s3:
    entry   a1, 48
    ones_in_q7 a15
    l32i    a6, a5, 0
1:
    // The frame's norms: group after group, its W channels 4 a step from its table.
    l32i    a7, a5, 28
    mov     a8, a4
    l32i    a9, a5, 12
2:
    l32i    a10, a5, 8
    srli    a10, a10, 2
    loopgtz a10, 3f
    l8ui    a11, a3, 0
    l8ui    a12, a3, 1
    l8ui    a13, a3, 2
    l8ui    a14, a3, 3
    add     a11, a11, a8
    add     a12, a12, a8
    add     a13, a13, a8
    add     a14, a14, a8
    l8ui    a11, a11, 0
    l8ui    a12, a12, 0
    l8ui    a13, a13, 0
    l8ui    a14, a14, 0
    s8i     a11, a7, 0
    s8i     a12, a7, 1
    s8i     a13, a7, 2
    s8i     a14, a7, 3
    addi    a3, a3, 4
    addi    a7, a7, 4
3:
    addmi   a8, a8, 256
    addi    a9, a9, -1
    bnez    a9, 2b
    // Then each block of 16: norm times weight, rounded, plus bias.
    l32i    a7, a5, 28
    l32i    a8, a5, 20
    l32i    a9, a5, 24
    l32i    a10, a5, 4
    srli    a10, a10, 4
    l32i    a13, a5, 16
    movi.n  a14, 1
    loopgtz a10, 4f
    ee.vld.128.ip   q0, a7, 16
    ee.vld.128.ip   q1, a8, 16
    ee.zero.qacc
    ee.vmulas.s8.qacc   q0, q1
    round_into  q2, a13, a14
    ee.vld.128.ip   q3, a9, 16
    ee.vadds.s8     q2, q2, q3
    ee.vst.128.ip   q2, a2, 16
4:
    l32i    a11, a5, 4
    l32i    a8, a5, 32
    sub     a8, a8, a11
    add     a2, a2, a8
    add     a3, a3, a8
    addi    a6, a6, -1
    bnez    a6, 1b
    retw.n
