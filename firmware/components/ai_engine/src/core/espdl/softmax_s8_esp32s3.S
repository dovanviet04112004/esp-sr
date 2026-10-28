// Strips of eight columns of esp-dl's int8 Softmax and the QuantizeLinear after it (KEHOACH 3.14 step 2.5), each
// float step the one softmax_block_s8 takes, in its order: table lookups by the input byte, column sums in row order,
// products by the column scale, plus 0.5, truncated, clipped at 127.
//
// void ai_engine_softmax_sums_s8_esp32s3(float *sums, const int8_t *x, const float *table, int rows, int stride)
// sums: eight floats, each the sum of its column's table values over rows, from 0; stride: bytes from row to row.
//
// void ai_engine_softmax_norm_s8_esp32s3(int8_t *y, const int8_t *x, const float *table, const float *scales,
//                                        int rows, const int32_t *strides)
// y: the eight columns, each value min(trunc(table[x] * scale + 0.5), 127); strides: x's, then y's.

    .text
    .align  4
    .global ai_engine_softmax_sums_s8_esp32s3
    .type   ai_engine_softmax_sums_s8_esp32s3, @function
    .global ai_engine_softmax_norm_s8_esp32s3
    .type   ai_engine_softmax_norm_s8_esp32s3, @function

ai_engine_softmax_sums_s8_esp32s3:
    entry   a1, 32
    movi.n  a8, 0
    wfr     f0, a8
    wfr     f1, a8
    wfr     f2, a8
    wfr     f3, a8
    wfr     f4, a8
    wfr     f5, a8
    wfr     f6, a8
    wfr     f7, a8
    // Each step for the eight columns at once: their bytes, table addresses, values, then the eight adds.
    loopgtz a5, 1f
    l8ui    a8, a3, 0
    l8ui    a9, a3, 1
    l8ui    a10, a3, 2
    l8ui    a11, a3, 3
    l8ui    a12, a3, 4
    l8ui    a13, a3, 5
    l8ui    a14, a3, 6
    l8ui    a15, a3, 7
    addx4   a8, a8, a4
    addx4   a9, a9, a4
    addx4   a10, a10, a4
    addx4   a11, a11, a4
    addx4   a12, a12, a4
    addx4   a13, a13, a4
    addx4   a14, a14, a4
    addx4   a15, a15, a4
    lsi     f8, a8, 0
    lsi     f9, a9, 0
    lsi     f10, a10, 0
    lsi     f11, a11, 0
    lsi     f12, a12, 0
    lsi     f13, a13, 0
    lsi     f14, a14, 0
    lsi     f15, a15, 0
    add.s   f0, f0, f8
    add.s   f1, f1, f9
    add.s   f2, f2, f10
    add.s   f3, f3, f11
    add.s   f4, f4, f12
    add.s   f5, f5, f13
    add.s   f6, f6, f14
    add.s   f7, f7, f15
    add     a3, a3, a6
1:
    ssi     f0, a2, 0
    ssi     f1, a2, 4
    ssi     f2, a2, 8
    ssi     f3, a2, 12
    ssi     f4, a2, 16
    ssi     f5, a2, 20
    ssi     f6, a2, 24
    ssi     f7, a2, 28
    retw.n

// Four columns from offset: scales f[first..first + 3], temporaries f9..f12, f8 holding 0.5, a15 holding 127.
.macro four_columns offset, s0, s1, s2, s3
    l8ui    a8, a3, \offset
    l8ui    a9, a3, \offset + 1
    l8ui    a10, a3, \offset + 2
    l8ui    a11, a3, \offset + 3
    addx4   a8, a8, a4
    addx4   a9, a9, a4
    addx4   a10, a10, a4
    addx4   a11, a11, a4
    lsi     f9, a8, 0
    lsi     f10, a9, 0
    lsi     f11, a10, 0
    lsi     f12, a11, 0
    mul.s   f9, f9, \s0
    mul.s   f10, f10, \s1
    mul.s   f11, f11, \s2
    mul.s   f12, f12, \s3
    add.s   f9, f9, f8
    add.s   f10, f10, f8
    add.s   f11, f11, f8
    add.s   f12, f12, f8
    trunc.s a8, f9, 0
    trunc.s a9, f10, 0
    trunc.s a10, f11, 0
    trunc.s a11, f12, 0
    min     a8, a8, a15
    min     a9, a9, a15
    min     a10, a10, a15
    min     a11, a11, a15
    s8i     a8, a2, \offset
    s8i     a9, a2, \offset + 1
    s8i     a10, a2, \offset + 2
    s8i     a11, a2, \offset + 3
.endm

    .align  4
ai_engine_softmax_norm_s8_esp32s3:
    entry   a1, 32
    l32i    a12, a7, 4
    l32i    a7, a7, 0
    lsi     f0, a5, 0
    lsi     f1, a5, 4
    lsi     f2, a5, 8
    lsi     f3, a5, 12
    lsi     f4, a5, 16
    lsi     f5, a5, 20
    lsi     f6, a5, 24
    lsi     f7, a5, 28
    // 0.5f is 0x3f000000.
    movi.n  a8, 0x3f
    slli    a8, a8, 24
    wfr     f8, a8
    movi    a15, 127
    loopgtz a6, 1f
    four_columns    0, f0, f1, f2, f3
    four_columns    4, f4, f5, f6, f7
    add     a3, a3, a7
    add     a2, a2, a12
1:
    retw.n
