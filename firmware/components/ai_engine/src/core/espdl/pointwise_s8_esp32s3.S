// PointwiseS8's rows (KEHOACH 3.14 step 2.5), each as esp-dl's int8 1x1 Conv gives it: bias in its QACC layout, the
// products, round half up; then the residual, if any, added saturating as Add does. run: rows, blocks of 16 outputs,
// input row stride in vectors, whole input vectors run ahead of the tail, tail (0), mac shift, bias, residual
// [rows][N], residual shift onto the sum's exponent, last block's outputs (16 or 8). out: [rows][N], 8-byte aligned.
//
// void ai_engine_pw_conv_rows_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
// Rows of K a multiple of 16 inputs, 16-byte aligned, no residual; run ends with out bytes past each row's blocks.
// A last block's outputs of 0 stores every block's 16 as two 8-byte halves.
//
// void ai_engine_pw_conv_k24_s8_esp32s3(...), ai_engine_pw_conv_k48_s8_esp32s3(...)
// void ai_engine_pw_add_k24_s8_esp32s3(...), ai_engine_pw_add_k48_s8_esp32s3(...)
// void ai_engine_pw_add_x_k24_s8_esp32s3(...), ai_engine_pw_add_x_k48_s8_esp32s3(...)
// Rows of 24 or 48 inputs held in q0, q5, q6, rows of 24 8-byte aligned; _add_ with a residual, _add_x_ shifting it
// right by 1 or more as RequantizeLinear rounds it.

#include "pie_s8_esp32s3.inc"

    .text
    .global ai_engine_pw_conv_rows_s8_esp32s3
    .type   ai_engine_pw_conv_rows_s8_esp32s3, @function
    .global ai_engine_pw_conv_k24_s8_esp32s3
    .type   ai_engine_pw_conv_k24_s8_esp32s3, @function
    .global ai_engine_pw_conv_k48_s8_esp32s3
    .type   ai_engine_pw_conv_k48_s8_esp32s3, @function
    .global ai_engine_pw_add_k24_s8_esp32s3
    .type   ai_engine_pw_add_k24_s8_esp32s3, @function
    .global ai_engine_pw_add_k48_s8_esp32s3
    .type   ai_engine_pw_add_k48_s8_esp32s3, @function
    .global ai_engine_pw_add_x_k24_s8_esp32s3
    .type   ai_engine_pw_add_x_k24_s8_esp32s3, @function
    .global ai_engine_pw_add_x_k48_s8_esp32s3
    .type   ai_engine_pw_add_x_k48_s8_esp32s3, @function

    .align  4
ai_engine_pw_conv_rows_s8_esp32s3:
    entry   a1, 48
    l32i    a6, a5, 0
    l32i    a13, a5, 20
    addi    a13, a13, -1
    l32i    a14, a5, 36
    ones_in_q7 a15
1:
    mov     a8, a4
    l32i    a9, a5, 24
    l32i    a7, a5, 4
2:
    bias_into_qacc  a9
    mov     a12, a3
    pointwise_products
    movi.n  a15, 1
    round_into  q3, a13, a15
    bnei    a14, 16, 9f
    ee.vst.128.ip   q3, a2, 16
    j       10f
9:
    ee.vst.l.64.ip  q3, a2, 8
    beqz    a14, 11f
    beqi    a7, 1, 10f
11:
    ee.vst.h.64.ip  q3, a2, 8
10:
    addi    a7, a7, -1
    bnez    a7, 2b
    l32i    a15, a5, 8
    slli    a15, a15, 4
    add     a3, a3, a15
    l32i    a15, a5, 40
    add     a2, a2, a15
    addi    a6, a6, -1
    bnez    a6, 1b
    retw.n


    .align  4
ai_engine_pw_conv_k24_s8_esp32s3:
    held_rows 24, 0

    .align  4
ai_engine_pw_conv_k48_s8_esp32s3:
    held_rows 48, 0

    .align  4
ai_engine_pw_add_k24_s8_esp32s3:
    held_rows 24, 1

    .align  4
ai_engine_pw_add_k48_s8_esp32s3:
    held_rows 48, 1

    .align  4
ai_engine_pw_add_x_k24_s8_esp32s3:
    held_rows 24, 2

    .align  4
ai_engine_pw_add_x_k48_s8_esp32s3:
    held_rows 48, 2
