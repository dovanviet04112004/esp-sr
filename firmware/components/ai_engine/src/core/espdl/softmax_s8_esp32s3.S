// Strips of eight columns of esp-dl's int8 Softmax and the QuantizeLinear after it (KEHOACH 3.14 step 2.5), each
// float step the one softmax_block_s8 takes, in its order: table lookups by the input byte, column sums in row order,
// products by the column scale, plus 0.5, truncated, clipped at 127.
//
// void ai_engine_softmax_sums_s8_esp32s3(float *sums, const int8_t *x, const float *table, int rows, int stride)
// sums: eight floats, each the sum of its column's table values over rows, from 0; stride: bytes from row to row.
//
// void ai_engine_softmax_norm_s8_esp32s3(int8_t *y, const int8_t *x, const float *table, const float *scales,
//                                        int rows, int stride)
// y: the eight columns, each value min(trunc(table[x] * scale + 0.5), 127); y and x share stride.

    .text
    .align  4
    .global ai_engine_softmax_sums_s8_esp32s3
    .type   ai_engine_softmax_sums_s8_esp32s3, @function
    .global ai_engine_softmax_norm_s8_esp32s3
    .type   ai_engine_softmax_norm_s8_esp32s3, @function

.macro add_column acc, tmp, at, offset
    l8ui    \at, a3, \offset
    addx4   \at, \at, a4
    lsi     \tmp, \at, 0
    add.s   \acc, \acc, \tmp
.endm

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
    loopgtz a5, 1f
    add_column  f0, f8, a8, 0
    add_column  f1, f9, a9, 1
    add_column  f2, f10, a10, 2
    add_column  f3, f11, a11, 3
    add_column  f4, f12, a12, 4
    add_column  f5, f13, a13, 5
    add_column  f6, f14, a14, 6
    add_column  f7, f15, a15, 7
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
    add     a2, a2, a7
1:
    retw.n
