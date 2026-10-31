// A 3x3 convolution of stride 2 from 3 interleaved input channels into 16 outputs a pixel (KEHOACH 3.18), summed as
// esp-dl 3.3.14's int8 kernels sum it: bias loaded into QACC in esp-dl's layout, one product a tap's channel, round
// half up as tie728_s8_vector_round_result does. A window row's 9 inputs lie together, so one unaligned load takes it.
//
// void ai_engine_stem3_s8_esp32s3(int8_t *out, const int32_t *run)      three window rows
// void ai_engine_stem3_top_s8_esp32s3(int8_t *out, const int32_t *run)  two, the first in the head's padding
// run: pixels, input step between pixels (bytes), each window row's first input, weights [row][tap][channel][16] of the
// rows read, mac shift less 1 (0 or more), bias. Rows share their offset within 16 bytes; out is 16-byte aligned.

#include "pie_s8_esp32s3.inc"

// 16 inputs from \at into \q, \at stepping on by a9 + 16; q4 is scratch.
.macro window_row q, at
    ee.ld.128.usar.ip   \q, \at, 16
    ee.vld.128.xp       q4, \at, a9
    ee.src.q            \q, \q, q4
.endm

// a2 out, a4 pixels left, a5 weights, a6 bias, a7 its pointer, a8 weight rows, a9 step less 16, a10-a12 window rows,
// a13 mac shift less 1, a15 one.
.macro stem3 rows, name
    .text
    .align  4
    .global \name
    .type   \name, @function
\name:
    entry   a1, 32
    ones_in_q7 a15
    l32i    a4, a3, 0
    l32i    a9, a3, 4
    addi    a9, a9, -16
    l32i    a10, a3, 8
    l32i    a11, a3, 12
    l32i    a12, a3, 16
    l32i    a5, a3, 20
    l32i    a13, a3, 24
    l32i    a6, a3, 28
    movi.n  a15, 1
    beqz    a4, 9f
1:
    mov     a7, a6
    bias_into_qacc  a7
    window_row q0, a10
    window_row q5, a11
    .if \rows == 3
    window_row q6, a12
    .endif
    mov     a8, a5
    ee.vld.128.ip   q1, a8, 16
    ee.vld.128.ip   q2, a8, 16
    fixed_lanes q0, 9, 0, (9 * \rows)
    fixed_lanes q5, 9, 9, (9 * \rows)
    .if \rows == 3
    fixed_lanes q6, 9, 18, 27
    .endif
    round_into  q3, a13, a15
    ee.vst.128.ip   q3, a2, 16
    addi    a4, a4, -1
    bnez    a4, 1b
9:
    retw.n
.endm

    stem3 3, ai_engine_stem3_s8_esp32s3
    stem3 2, ai_engine_stem3_top_s8_esp32s3
