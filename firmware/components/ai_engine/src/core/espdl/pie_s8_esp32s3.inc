// PIE macros the int8 pointwise kernels share (KEHOACH 3.14 step 2.5): QACC bias and rounding as esp-dl's tie728
// kernels take them, the products over held inputs, and the held rows of conv_row_s8 and pointwise_s8.

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

// The products of a pixel over V whole input vectors from a12, unrolled: each product loads the weight row two
// steps ahead from a8, the 16th of a vector the next input vector, then one load the next row; none past the last.
.macro unrolled_products V
    ee.vld.128.ip   q0, a12, 16
    ee.vld.128.ip   q1, a8, 16
    ee.vld.128.ip   q2, a8, 16
    .rept \V - 1
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
    .endr
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
.endm

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
    .if \c == 12
    fixed_lanes q0, 12, \k, \total
    .else
    fixed_lanes q0, 16, \k, \total
    .endif
    .if \c == 24
    fixed_lanes q5, 8, (\k + 16), \total
    .elseif \c == 48
    fixed_lanes q5, 16, (\k + 16), \total
    fixed_lanes q6, 16, (\k + 32), \total
    .endif
.endm

// One block of 16 outputs (8 stored when width is 8) over the held inputs; the last of a row loads no weight past it.
.macro held_block k, residual, last, width
    bias_into_qacc  a7
    .if \last
    fixed_products \k, 0, \k
    .else
    fixed_products \k, 0, (\k + 2)
    .endif
    round_into  q3, a13, a15
    .if \residual
    ee.vld.l.64.ip  q4, a11, 8
    .if \width == 16
    ee.vld.h.64.ip  q4, a11, 8
    .endif
    .if \residual == 2
    ee.zero.qacc
    ee.vmulas.s8.qacc   q4, q7
    round_into  q4, a14, a15
    .endif
    ee.vadds.s8     q3, q3, q4
    .endif
    .if \width == 12
    ee.movi.32.a    q3, a12, 0
    s32i    a12, a2, 0
    ee.movi.32.a    q3, a12, 1
    s32i    a12, a2, 4
    ee.movi.32.a    q3, a12, 2
    s32i    a12, a2, 8
    addi    a2, a2, 12
    .else
    ee.vst.l.64.ip  q3, a2, 8
    .if \width == 16
    ee.vst.h.64.ip  q3, a2, 8
    .endif
    .endif
.endm

// a9 rows, a10 blocks less one, a6 bias, a7 its pointer, a4 weights, a8 their pointer, a11 residual, a13 and a14 the
// shifts less 1, a5 the last block's outputs; residual 0 none, 1 added, 2 shifted then added; twelve: 12 outputs.
.macro held_rows k, residual, twelve=0
    entry   a1, 48
    ones_in_q7 a7
    l32i    a9, a5, 0
    l32i    a10, a5, 4
    addi    a10, a10, -1
    l32i    a13, a5, 20
    addi    a13, a13, -1
    l32i    a6, a5, 24
    l32i    a11, a5, 28
    l32i    a14, a5, 32
    addi    a14, a14, -1
    l32i    a5, a5, 36
    movi.n  a15, 1
    beqz    a9, 9f
1:
    .if \k == 12
    l32i    a12, a3, 0
    ee.movi.32.q    q0, a12, 0
    l32i    a12, a3, 4
    ee.movi.32.q    q0, a12, 1
    l32i    a12, a3, 8
    ee.movi.32.q    q0, a12, 2
    addi    a3, a3, 12
    .elseif \k == 24
    ee.vld.l.64.ip  q0, a3, 8
    ee.vld.h.64.ip  q0, a3, 8
    ee.vld.l.64.ip  q5, a3, 8
    .else
    ee.vld.128.ip   q0, a3, 16
    ee.vld.128.ip   q5, a3, 16
    ee.vld.128.ip   q6, a3, 16
    .endif
    mov     a8, a4
    mov     a7, a6
    ee.vld.128.ip   q1, a8, 16
    ee.vld.128.ip   q2, a8, 16
    .if \k < 48
    loopgtz a10, 2f
    held_block \k, \residual, 0, 16
2:
    .else
    mov     a12, a10
    beqz    a12, 2f
3:
    held_block 48, \residual, 0, 16
    addi    a12, a12, -1
    bnez    a12, 3b
2:
    .endif
    .if \twelve
    held_block \k, \residual, 1, 12
    .else
    bnei    a5, 16, 4f
    held_block \k, \residual, 1, 16
    j       5f
4:
    held_block \k, \residual, 1, 8
5:
    .endif
    addi    a9, a9, -1
    bnez    a9, 1b
9:
    retw.n
.endm
