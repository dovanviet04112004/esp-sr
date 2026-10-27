// Row kernels of ai_engine's pool ops (KEHOACH 3.14 step 2.5), each product as esp-dl 3.3.11's int8 Mul gives it
// (tie728_s8_mul_w1_16_w2_16: QACC product, round half up as tie728_s8_vector_round_result does, saturated).
//
// void ai_engine_weighted_stats_row_s8_esp32s3(int16_t *sums, const int8_t *x, const int8_t *p, const int32_t *run)
// run: 16-channel groups, the shifts of x * x, of p * x and of p * (x * x), each 1 or more. sums: per group the int16
// sums of p * x over channels 0-7 and 8-15, then those of p * (x * x), each row's products added; pointers 16-byte
// aligned. ReduceSum's int32 sums are these while a sum stays within int16.
//
// void ai_engine_row_sums_s8_esp32s3(int16_t *sums, const int8_t *x, int groups)
// sums: per group the int16 sums of x over channels 0-7 and 8-15, the row added.
//
// void ai_engine_centred_squares_row_s8_esp32s3(int16_t *sums, const int8_t *x, const int8_t *mean, const int32_t *run)
// run: groups, the shift of d * d (1 or more). d = x - mean saturating, as esp-dl's int8 Sub gives it, then d * d as
// its Mul does, added into per-group int16 sums as above.

    .text
    .align  4
    .global ai_engine_weighted_stats_row_s8_esp32s3
    .type   ai_engine_weighted_stats_row_s8_esp32s3, @function
    .global ai_engine_row_sums_s8_esp32s3
    .type   ai_engine_row_sums_s8_esp32s3, @function
    .global ai_engine_centred_squares_row_s8_esp32s3
    .type   ai_engine_centred_squares_row_s8_esp32s3, @function

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

// The int8 products in \low, sign-extended against q6 (zero), added to two int16 vectors of sums: a2 reads, a11
// writes.
.macro add_to_sums low
    ee.vcmp.lt.s8   q5, \low, q6
    ee.vzip.8       \low, q5
    ee.vld.128.ip   q0, a2, 16
    ee.vadds.s16    q0, q0, \low
    ee.vst.128.ip   q0, a11, 16
    ee.vld.128.ip   q0, a2, 16
    ee.vadds.s16    q0, q0, q5
    ee.vst.128.ip   q0, a11, 16
.endm

ai_engine_weighted_stats_row_s8_esp32s3:
    entry   a1, 48
    l32i    a6, a5, 0
    l32i    a13, a5, 4
    addi    a13, a13, -1
    l32i    a14, a5, 8
    addi    a14, a14, -1
    l32i    a15, a5, 12
    addi    a15, a15, -1
    movi.n  a12, 1
    mov     a11, a2
    ones_in_q7 a10
    ee.zero.q   q6
    loopgtz a6, 1f
    ee.vld.128.ip   q0, a3, 16
    ee.vld.128.ip   q1, a4, 16
    ee.zero.qacc
    ee.vmulas.s8.qacc   q0, q0
    round_into  q2, a13, a12
    ee.zero.qacc
    ee.vmulas.s8.qacc   q1, q0
    round_into  q3, a14, a12
    ee.zero.qacc
    ee.vmulas.s8.qacc   q1, q2
    round_into  q4, a15, a12
    add_to_sums q3
    add_to_sums q4
1:
    retw.n

    .align  4
ai_engine_row_sums_s8_esp32s3:
    entry   a1, 32
    mov     a11, a2
    ee.zero.q   q6
    loopgtz a4, 1f
    ee.vld.128.ip   q3, a3, 16
    add_to_sums q3
1:
    retw.n

    .align  4
ai_engine_centred_squares_row_s8_esp32s3:
    entry   a1, 32
    l32i    a6, a5, 0
    l32i    a13, a5, 4
    addi    a13, a13, -1
    movi.n  a12, 1
    mov     a11, a2
    ones_in_q7 a10
    ee.zero.q   q6
    loopgtz a6, 1f
    ee.vld.128.ip   q0, a3, 16
    ee.vld.128.ip   q1, a4, 16
    ee.vsubs.s8     q2, q0, q1
    ee.zero.qacc
    ee.vmulas.s8.qacc   q2, q2
    round_into  q3, a13, a12
    add_to_sums q3
1:
    retw.n
