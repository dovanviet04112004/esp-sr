// Depthwise row kernels of ai_engine's own ops and their padded rows (KEHOACH 3.14 step 2.5), each taking esp-dl
// 3.3.11's int8 steps for the op it stands for: QACC products, round half up as tie728_s8_vector_round_result does.
// Pointers 16-byte aligned unless said; a pixel is V 16-byte vectors.
//
// void ai_engine_dw3x3_row_s8_esp32s3(int8_t *out, const int8_t *const *rows, const int8_t *taps, const int32_t *run)
// rows: three [F + 2][16 V] rows, t - 1, t, t + 1, a zero pixel at each end; taps: [9][16 V], tap 3 * kh + kw;
// run: F pixels, V, mac shift, 1 or more. out: [F][16 V]. Depthwise 3x3 without bias, as tie728_s8_depthwise_conv2d_33c1.
//
// void ai_engine_dw3x3_bias_row_s8_esp32s3(int8_t *out, const int8_t *const *rows, const int8_t *taps,
//                                          const int32_t *run)
// As the one above, run also holding the bias in esp-dl's QACC layout, 64 bytes a vector, loaded ahead of the products.
//
// void ai_engine_spread_row_s8_esp32s3(int8_t *out, const int8_t *in, int pixels)
// Each byte of in into all 16 bytes of its out pixel, as esp-dl's Add of an int8 map by zeros [16] broadcasts it.
//
// void ai_engine_dwk_row_s8_esp32s3(int8_t *out, const int8_t *const *rows, const int8_t *taps, const int32_t *run)
// rows: k [F][16 V] rows; taps: [V][k][16]; run: F pixels, V, mac shift (1 or more), bias in esp-dl's QACC layout,
// k (2 or 4). out: [F][16 V]. A depthwise k x 1 at a stride of k, as tie728_s8_depthwise_conv2d_hwc1 with bias.
//
// void ai_engine_pad_row_s8_esp32s3(int8_t *out, const int8_t *in, const int32_t *run)
// run: pixels, whole 16-byte vectors of a pixel, 8 bytes more or not (1 or 0). in: [pixels][C], 8-byte aligned;
// out: [pixels][C rounded up to 16], the bytes past C left as they are.

    .text
    .align  4
    .global ai_engine_dw3x3_row_s8_esp32s3
    .type   ai_engine_dw3x3_row_s8_esp32s3, @function
    .global ai_engine_dw3x3_bias_row_s8_esp32s3
    .type   ai_engine_dw3x3_bias_row_s8_esp32s3, @function
    .global ai_engine_spread_row_s8_esp32s3
    .type   ai_engine_spread_row_s8_esp32s3, @function
    .global ai_engine_dwk_row_s8_esp32s3
    .type   ai_engine_dwk_row_s8_esp32s3, @function
    .global ai_engine_pad_row_s8_esp32s3
    .type   ai_engine_pad_row_s8_esp32s3, @function

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

// Loads the run, then each pixel's V vectors; biased takes each vector's bias from a5 instead of a zero QACC.
.macro dw3x3_rows biased
    l32i    a10, a3, 0
    l32i    a11, a3, 4
    l32i    a12, a3, 8
    l32i    a6, a5, 0
    l32i    a7, a5, 4
    l32i    a13, a5, 8
  .if \biased
    l32i    a5, a5, 12
  .endif
    addi    a13, a13, -1
    movi.n  a14, 1
    slli    a9, a7, 4
    // A row's third load steps back to its next vector, the ninth tap's to the next vector's first.
    movi.n  a15, 16
    sub     a8, a15, a9
    sub     a8, a8, a9
    slli    a3, a9, 3
    sub     a3, a15, a3
    ones_in_q7 a15
1:
    mov     a15, a4
    loopgtz a7, 2f
  .if \biased
    bias_into_qacc  a5
  .else
    ee.zero.qacc
  .endif
    ee.vld.128.xp   q0, a10, a9
    ee.vld.128.xp   q1, a15, a9
    ee.vld.128.xp   q2, a10, a9
    ee.vld.128.xp   q3, a15, a9
    ee.vmulas.s8.qacc.ld.xp q4, a10, a8, q0, q1
    ee.vld.128.xp   q5, a15, a9
    ee.vmulas.s8.qacc.ld.xp q0, a11, a9, q2, q3
    ee.vld.128.xp   q1, a15, a9
    ee.vmulas.s8.qacc.ld.xp q2, a11, a9, q4, q5
    ee.vld.128.xp   q3, a15, a9
    ee.vmulas.s8.qacc.ld.xp q4, a11, a8, q0, q1
    ee.vld.128.xp   q5, a15, a9
    ee.vmulas.s8.qacc.ld.xp q0, a12, a9, q2, q3
    ee.vld.128.xp   q1, a15, a9
    ee.vmulas.s8.qacc.ld.xp q2, a12, a9, q4, q5
    ee.vld.128.xp   q3, a15, a9
    ee.vmulas.s8.qacc.ld.xp q4, a12, a8, q0, q1
    ee.vld.128.xp   q5, a15, a3
    ee.vmulas.s8.qacc   q2, q3
    ee.vmulas.s8.qacc   q4, q5
    round_into  q6, a13, a14
    ee.vst.128.ip   q6, a2, 16
2:
  .if \biased
    slli    a15, a7, 6
    sub     a5, a5, a15
  .endif
    addi    a6, a6, -1
    bnez    a6, 1b
.endm

ai_engine_dw3x3_row_s8_esp32s3:
    entry   a1, 48
    dw3x3_rows 0
    retw.n

    .align  4
ai_engine_dw3x3_bias_row_s8_esp32s3:
    entry   a1, 48
    dw3x3_rows 1
    retw.n

    .align  4
ai_engine_spread_row_s8_esp32s3:
    entry   a1, 32
    loopgtz a4, 1f
    ee.vldbc.8.ip   q0, a3, 1
    ee.vst.128.ip   q0, a2, 16
1:
    retw.n

// One tap of the k: the row's vector from \row, its tap from a15, both stepping on.
.macro dwk_tap row
    ee.vld.128.ip   q0, \row, 16
    ee.vld.128.ip   q1, a15, 16
    ee.vmulas.s8.qacc   q0, q1
.endm

.macro dwk_rows k
1:
    mov     a15, a4
    mov     a8, a5
    loopgtz a7, 2f
    bias_into_qacc  a8
    dwk_tap a9
    dwk_tap a10
  .if \k == 4
    dwk_tap a11
    dwk_tap a12
  .endif
    round_into  q6, a13, a14
    ee.vst.128.ip   q6, a2, 16
2:
    addi    a6, a6, -1
    bnez    a6, 1b
.endm

    .align  4
ai_engine_dwk_row_s8_esp32s3:
    entry   a1, 48
    l32i    a9, a3, 0
    l32i    a10, a3, 4
    l32i    a6, a5, 0
    l32i    a7, a5, 4
    l32i    a13, a5, 8
    l32i    a8, a5, 16
    l32i    a5, a5, 12
    addi    a13, a13, -1
    movi.n  a14, 1
    ones_in_q7 a15
    beqi    a8, 2, 3f
    l32i    a11, a3, 8
    l32i    a12, a3, 12
    dwk_rows 4
    retw.n
3:
    dwk_rows 2
    retw.n

    .align  4
ai_engine_pad_row_s8_esp32s3:
    entry   a1, 32
    l32i    a6, a4, 0
    l32i    a7, a4, 4
    l32i    a8, a4, 8
1:
    loopgtz a7, 2f
    ee.vld.l.64.ip  q0, a3, 8
    ee.vld.h.64.ip  q0, a3, 8
    ee.vst.128.ip   q0, a2, 16
2:
    beqz    a8, 3f
    ee.vld.l.64.ip  q0, a3, 8
    ee.vst.l.64.ip  q0, a2, 16
3:
    addi    a6, a6, -1
    bnez    a6, 1b
    retw.n
