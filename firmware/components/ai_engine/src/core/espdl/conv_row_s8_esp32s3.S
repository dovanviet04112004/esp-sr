// Pointwise row kernels of ConvBlock (KEHOACH 3.14 step 2.5), each block of 16 outputs as esp-dl 3.3.11's aligned
// int8 1x1 kernel sums it: bias loaded into QACC in esp-dl's layout, one product a step, round half up as
// tie728_s8_vector_round_result does. Weights [blocks][C][16], C the inputs summed; pointers 16-byte aligned unless
// said.
//
// void ai_engine_pw_norm_row_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
// run: pixels, blocks, input pixel stride in vectors, whole input vectors run ahead of the tail, tail (0: 16 inputs,
// 1: 8), mac shift, bias, norm weights [16 blocks], norm bias, norm shift; shifts 1 or more. Then ReLU and the
// norm's depthwise 1x1 with bias, as tie728_s8_depthwise_conv2d_hwc1. out: [pixels][16 blocks].
//
// void ai_engine_pw_residual_row_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
// run: pixels, blocks, input pixel stride in vectors, whole input vectors, tail, mac shift, bias, x [pixels][16
// blocks], x shift onto the sum's exponent (0 or more), last block's outputs (16 or 8). x as esp-dl's int8
// RequantizeLinear rounds it, added saturating as its Add does, then max(0, .). out: [pixels][C], 8-byte aligned.
//
// void ai_engine_pw_bias_row_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
// run: rows, blocks, input row stride in vectors, whole input vectors, tail, mac shift, an int8 bias [16 blocks] on
// the output's exponent or 0, ReLU or not (1 or 0), last block's outputs (16 or 8). Each row as esp-dl's int8
// MatMul of it by constant weights gives it, its ReLU, then the bias added saturating as Add does. out: [rows][N].

    .text
    .align  4
    .global ai_engine_pw_norm_row_s8_esp32s3
    .type   ai_engine_pw_norm_row_s8_esp32s3, @function
    .global ai_engine_pw_residual_row_s8_esp32s3
    .type   ai_engine_pw_residual_row_s8_esp32s3, @function
    .global ai_engine_pw_bias_row_s8_esp32s3
    .type   ai_engine_pw_bias_row_s8_esp32s3, @function

.macro ones_in_q7 tmp
    movi    \tmp, 257
    ee.movi.32.q    q7, \tmp, 0
    ee.movi.32.q    q7, \tmp, 1
    ee.movi.32.q    q7, \tmp, 2
    ee.movi.32.q    q7, \tmp, 3
    movi.n  \tmp, 16
    wsr.sar \tmp
    ee.vsl.32       q6, q7
    ee.orq          q7, q6, q7
.endm

// QACC >> (less + 1) rounded half up into out: q7 holds 1 in every byte, one holds 1.
.macro round_into out, less, one
    ee.srcmb.s8.qacc    \out, \less, 0
    ee.vsmulas.s8.qacc  q7, q7, 0
    ee.srcmb.s8.qacc    \out, \one, 0
.endm

.macro bias_into_qacc ptr
    ee.ld.qacc_l.l.128.ip   \ptr, 16
    ee.ld.qacc_l.h.32.ip    \ptr, 16
    ee.ld.qacc_h.l.128.ip   \ptr, 16
    ee.ld.qacc_h.h.32.ip    \ptr, 16
.endm

// QACC += the pixel's inputs from a12 times the block's weight rows from a8, as tie728_s8_conv2d_11c16 takes them:
// each whole vector's 16th product loads the next vector, then the tail of 16 or 8 inputs. a15 is scratch.
.macro pointwise_products
    ee.vld.128.ip   q0, a12, 16
    ee.vld.128.ip   q1, a8, 16
    ee.vld.128.ip   q2, a8, 16
    l32i    a15, a5, 12
    loopgtz a15, 4f
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
    ee.vsmulas.s8.qacc.ld.incp  q1, a8, q1, q0, 12
    ee.vsmulas.s8.qacc.ld.incp  q2, a8, q2, q0, 13
    ee.vsmulas.s8.qacc.ld.incp  q1, a8, q1, q0, 14
    ee.vsmulas.s8.qacc.ld.incp  q0, a12, q2, q0, 15
    ee.vld.128.ip   q2, a8, 16
4:
    l32i    a15, a5, 16
    bnez    a15, 5f
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
    ee.vsmulas.s8.qacc.ld.incp  q1, a8, q1, q0, 12
    ee.vsmulas.s8.qacc.ld.incp  q2, a8, q2, q0, 13
    ee.vsmulas.s8.qacc  q1, q0, 14
    ee.vsmulas.s8.qacc  q2, q0, 15
    j       6f
5:
    ee.vsmulas.s8.qacc.ld.incp  q1, a8, q1, q0, 0
    ee.vsmulas.s8.qacc.ld.incp  q2, a8, q2, q0, 1
    ee.vsmulas.s8.qacc.ld.incp  q1, a8, q1, q0, 2
    ee.vsmulas.s8.qacc.ld.incp  q2, a8, q2, q0, 3
    ee.vsmulas.s8.qacc.ld.incp  q1, a8, q1, q0, 4
    ee.vsmulas.s8.qacc.ld.incp  q2, a8, q2, q0, 5
    ee.vsmulas.s8.qacc  q1, q0, 6
    ee.vsmulas.s8.qacc  q2, q0, 7
6:
.endm

ai_engine_pw_norm_row_s8_esp32s3:
    entry   a1, 48
    l32i    a6, a5, 0
    l32i    a13, a5, 20
    addi    a13, a13, -1
    l32i    a14, a5, 36
    addi    a14, a14, -1
    ones_in_q7 a15
1:
    mov     a8, a4
    l32i    a9, a5, 24
    l32i    a10, a5, 28
    l32i    a11, a5, 32
    l32i    a7, a5, 4
2:
    bias_into_qacc  a9
    mov     a12, a3
    pointwise_products
    movi.n  a15, 1
    round_into  q3, a13, a15
    movi.n  a15, 0
    ee.vrelu.s8     q3, a15, a15
    bias_into_qacc  a11
    ee.vld.128.ip   q4, a10, 16
    ee.vmulas.s8.qacc   q3, q4
    movi.n  a15, 1
    round_into  q5, a14, a15
    ee.vst.128.ip   q5, a2, 16
    addi    a7, a7, -1
    bnez    a7, 2b
    l32i    a15, a5, 8
    slli    a15, a15, 4
    add     a3, a3, a15
    addi    a6, a6, -1
    bnez    a6, 1b
    retw.n

    .align  4
ai_engine_pw_residual_row_s8_esp32s3:
    entry   a1, 48
    l32i    a6, a5, 0
    l32i    a13, a5, 20
    addi    a13, a13, -1
    l32i    a10, a5, 28
    l32i    a14, a5, 32
    addi    a11, a14, -1
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
    beqz    a14, 7f
    ee.ldqa.s8.128.ip   a10, 16
    round_into  q4, a11, a15
    j       8f
7:
    ee.vld.128.ip   q4, a10, 16
8:
    ee.vadds.s8     q5, q3, q4
    movi.n  a15, 0
    ee.vrelu.s8     q5, a15, a15
    l32i    a15, a5, 36
    bnei    a15, 16, 9f
    ee.vst.128.ip   q5, a2, 16
    j       10f
9:
    ee.vst.l.64.ip  q5, a2, 8
    beqi    a7, 1, 10f
    ee.vst.h.64.ip  q5, a2, 8
10:
    addi    a7, a7, -1
    bnez    a7, 2b
    l32i    a15, a5, 8
    slli    a15, a15, 4
    add     a3, a3, a15
    addi    a6, a6, -1
    bnez    a6, 1b
    retw.n

    .align  4
ai_engine_pw_bias_row_s8_esp32s3:
    entry   a1, 48
    l32i    a6, a5, 0
    l32i    a13, a5, 20
    addi    a13, a13, -1
    l32i    a11, a5, 24
    l32i    a14, a5, 28
    ones_in_q7 a15
1:
    mov     a8, a4
    mov     a10, a11
    l32i    a7, a5, 4
2:
    ee.zero.qacc
    mov     a12, a3
    pointwise_products
    movi.n  a15, 1
    round_into  q3, a13, a15
    beqz    a14, 7f
    movi.n  a15, 0
    ee.vrelu.s8     q3, a15, a15
7:
    beqz    a11, 8f
    ee.vld.128.ip   q4, a10, 16
    ee.vadds.s8     q3, q3, q4
8:
    l32i    a15, a5, 32
    bnei    a15, 16, 9f
    ee.vst.128.ip   q3, a2, 16
    j       10f
9:
    ee.vst.l.64.ip  q3, a2, 8
    beqi    a7, 1, 10f
    ee.vst.h.64.ip  q3, a2, 8
10:
    addi    a7, a7, -1
    bnez    a7, 2b
    l32i    a15, a5, 8
    slli    a15, a15, 4
    add     a3, a3, a15
    addi    a6, a6, -1
    bnez    a6, 1b
    retw.n

// ConvBlock's pointwise rows for a pixel of 24 or 48 channels held in q0, q5, q6, every product unrolled: weight row k
// multiplies q1 when k is even, q2 when odd, and reloads that register for k + 2, so no load is used the next step.
//
// void ai_engine_pw_norm_c24_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
// void ai_engine_pw_norm_c48_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
// As ai_engine_pw_norm_row_s8_esp32s3, its run alike.
//
// void ai_engine_pw_residual_c24_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
// void ai_engine_pw_residual_c48_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
// void ai_engine_pw_residual_x_c24_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
// void ai_engine_pw_residual_x_c48_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run)
// As ai_engine_pw_residual_row_s8_esp32s3, its run alike; _x for an x shift of 1 or more, the others for 0.

    .global ai_engine_pw_norm_c24_s8_esp32s3
    .type   ai_engine_pw_norm_c24_s8_esp32s3, @function
    .global ai_engine_pw_norm_c48_s8_esp32s3
    .type   ai_engine_pw_norm_c48_s8_esp32s3, @function
    .global ai_engine_pw_residual_c24_s8_esp32s3
    .type   ai_engine_pw_residual_c24_s8_esp32s3, @function
    .global ai_engine_pw_residual_c48_s8_esp32s3
    .type   ai_engine_pw_residual_c48_s8_esp32s3, @function
    .global ai_engine_pw_residual_x_c24_s8_esp32s3
    .type   ai_engine_pw_residual_x_c24_s8_esp32s3, @function
    .global ai_engine_pw_residual_x_c48_s8_esp32s3
    .type   ai_engine_pw_residual_x_c48_s8_esp32s3, @function

// Products k .. k + count - 1 of a pixel's total, on lanes 0 .. count - 1 of in.
.macro fixed_lanes in, count, k, total
    .irp lane, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15
    .if \lane < \count
    .if ((\k + \lane) % 2) == 0
    .if (\k + \lane + 2) < \total
    ee.vsmulas.s8.qacc.ld.incp  q1, a8, q1, \in, \lane
    .else
    ee.vsmulas.s8.qacc  q1, \in, \lane
    .endif
    .else
    .if (\k + \lane + 2) < \total
    ee.vsmulas.s8.qacc.ld.incp  q2, a8, q2, \in, \lane
    .else
    ee.vsmulas.s8.qacc  q2, \in, \lane
    .endif
    .endif
    .endif
    .endr
.endm

// One block's products over the held inputs, from product k of the pixel.
.macro fixed_products c, k, total
    fixed_lanes q0, 16, \k, \total
    .if \c == 24
    fixed_lanes q5, 8, (\k + 16), \total
    .else
    fixed_lanes q5, 16, (\k + 16), \total
    fixed_lanes q6, 16, (\k + 32), \total
    .endif
.endm

// The pixel's inputs, then its first two weight rows.
.macro fixed_inputs c
    ee.vld.128.ip   q0, a3, 16
    ee.vld.128.ip   q5, a3, 16
    .if \c == 48
    ee.vld.128.ip   q6, a3, 16
    .endif
    ee.vld.128.ip   q1, a8, 16
    ee.vld.128.ip   q2, a8, 16
.endm

// reg -= bytes, a multiple of 16 below 4096.
.macro rewind reg, bytes
    .if \bytes >= 256
    addmi   \reg, \reg, -((\bytes) / 256 * 256)
    .endif
    .if ((\bytes) % 256) > 128
    addi    \reg, \reg, -128
    addi    \reg, \reg, -((\bytes) % 256 - 128)
    .elseif ((\bytes) % 256) > 0
    addi    \reg, \reg, -((\bytes) % 256)
    .endif
.endm

// a9 pixels; a7 bias, a8 weights, a5 norm weights, a6 norm bias, each rewound after a pixel; a13, a14 shifts less 1.
.macro fixed_norm c
    entry   a1, 48
    ones_in_q7 a7
    l32i    a9, a5, 0
    l32i    a13, a5, 20
    addi    a13, a13, -1
    l32i    a7, a5, 24
    l32i    a14, a5, 36
    addi    a14, a14, -1
    l32i    a6, a5, 32
    l32i    a5, a5, 28
    mov     a8, a4
    movi.n  a12, 0
    movi.n  a15, 1
    beqz    a9, 9f
1:
    fixed_inputs \c
    .irp b, 0, 1, 2
    .if \b < (\c + 15) / 16
    bias_into_qacc  a7
    ee.vld.128.ip   q4, a5, 16
    fixed_products \c, (\b * \c), (((\c + 15) / 16) * \c)
    round_into  q3, a13, a15
    ee.vrelu.s8     q3, a12, a12
    bias_into_qacc  a6
    ee.vmulas.s8.qacc   q3, q4
    round_into  q3, a14, a15
    ee.vst.128.ip   q3, a2, 16
    .endif
    .endr
    rewind  a8, (((\c + 15) / 16) * \c * 16)
    rewind  a7, (((\c + 15) / 16) * 64)
    rewind  a5, (((\c + 15) / 16) * 16)
    rewind  a6, (((\c + 15) / 16) * 64)
    addi    a9, a9, -1
    bnez    a9, 1b
9:
    retw.n
.endm

// a9 pixels; a7 bias and a8 weights rewound after a pixel; a10 x; a11 x shift less 1 when shifted; a13 mac shift less 1.
.macro fixed_residual c, shifted
    entry   a1, 48
    ones_in_q7 a7
    l32i    a9, a5, 0
    l32i    a13, a5, 20
    addi    a13, a13, -1
    l32i    a7, a5, 24
    l32i    a10, a5, 28
    l32i    a11, a5, 32
    addi    a11, a11, -1
    mov     a8, a4
    movi.n  a12, 0
    movi.n  a15, 1
    beqz    a9, 9f
1:
    fixed_inputs \c
    .irp b, 0, 1, 2
    .if \b < (\c + 15) / 16
    bias_into_qacc  a7
    fixed_products \c, (\b * \c), (((\c + 15) / 16) * \c)
    round_into  q3, a13, a15
    .if \shifted
    ee.ldqa.s8.128.ip   a10, 16
    round_into  q4, a11, a15
    .else
    ee.vld.128.ip   q4, a10, 16
    .endif
    ee.vadds.s8     q3, q3, q4
    ee.vrelu.s8     q3, a12, a12
    .if \c == 48
    ee.vst.128.ip   q3, a2, 16
    .elseif \b == 0
    ee.vst.l.64.ip  q3, a2, 8
    ee.vst.h.64.ip  q3, a2, 8
    .else
    ee.vst.l.64.ip  q3, a2, 8
    .endif
    .endif
    .endr
    rewind  a8, (((\c + 15) / 16) * \c * 16)
    rewind  a7, (((\c + 15) / 16) * 64)
    addi    a9, a9, -1
    bnez    a9, 1b
9:
    retw.n
.endm

    .align  4
ai_engine_pw_norm_c24_s8_esp32s3:
    fixed_norm 24

    .align  4
ai_engine_pw_norm_c48_s8_esp32s3:
    fixed_norm 48

    .align  4
ai_engine_pw_residual_c24_s8_esp32s3:
    fixed_residual 24, 0

    .align  4
ai_engine_pw_residual_c48_s8_esp32s3:
    fixed_residual 48, 0

    .align  4
ai_engine_pw_residual_x_c24_s8_esp32s3:
    fixed_residual 24, 1

    .align  4
ai_engine_pw_residual_x_c48_s8_esp32s3:
    fixed_residual 48, 1
