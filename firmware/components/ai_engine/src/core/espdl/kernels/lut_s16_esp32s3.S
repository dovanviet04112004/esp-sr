// An int16 table of step 2^shift over int16 values in place (KEHOACH 3.18), as esp-dl's lut_s16_nearest_neighbor
// rounds on the S3: table[((v ^ 0x8000) + half) >> shift], half = 2^(shift - 1) or 0, which is
// middle[(v + half) >> shift] with an arithmetic shift and middle = table + 2^(15 - shift). Four values a step.
//
// void ai_engine_lut_s16_esp32s3(int16_t *values, int n, const int16_t *middle, int shift)

    .text
    .align  4
    .global ai_engine_lut_s16_esp32s3
    .type   ai_engine_lut_s16_esp32s3, @function

// a2 values, a3 n, a4 middle, a5 shift, a6 steps of four, a7 half, a12 the values left past the steps.
ai_engine_lut_s16_esp32s3:
    entry   a1, 32
    movi.n  a7, 0
    beqz    a5, 1f
    addi    a6, a5, -1
    movi.n  a7, 1
    ssl     a6
    sll     a7, a7
1:
    ssr     a5
    srli    a6, a3, 2
    extui   a12, a3, 0, 2
    loopgtz a6, 2f
    l16si   a8, a2, 0
    l16si   a9, a2, 2
    l16si   a10, a2, 4
    l16si   a11, a2, 6
    add     a8, a8, a7
    add     a9, a9, a7
    add     a10, a10, a7
    add     a11, a11, a7
    sra     a8, a8
    sra     a9, a9
    sra     a10, a10
    sra     a11, a11
    addx2   a8, a8, a4
    addx2   a9, a9, a4
    addx2   a10, a10, a4
    addx2   a11, a11, a4
    l16si   a8, a8, 0
    l16si   a9, a9, 0
    l16si   a10, a10, 0
    l16si   a11, a11, 0
    s16i    a8, a2, 0
    s16i    a9, a2, 2
    s16i    a10, a2, 4
    s16i    a11, a2, 6
    addi    a2, a2, 8
2:
    loopgtz a12, 3f
    l16si   a8, a2, 0
    add     a8, a8, a7
    sra     a8, a8
    addx2   a8, a8, a4
    l16si   a8, a8, 0
    s16i    a8, a2, 0
    addi    a2, a2, 2
3:
    retw.n
