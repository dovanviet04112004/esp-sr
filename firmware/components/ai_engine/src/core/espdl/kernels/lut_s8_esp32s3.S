// An int8 table over int8 values in place (KEHOACH 3.18): each value's byte u becomes turned[u], turned being esp-dl's
// table rotated by 128 bytes, so u indexes it as lut_s8's table[u ^ 128]. Four values a step, their loads, sums,
// lookups and stores interleaved so no load waits on the one before.
//
// void ai_engine_lut_s8_esp32s3(int8_t *values, int n, const int8_t *turned)

    .text
    .align  4
    .global ai_engine_lut_s8_esp32s3
    .type   ai_engine_lut_s8_esp32s3, @function

// a2 values, a3 n, a4 turned, a5 steps of four, a6 the values left past them.
ai_engine_lut_s8_esp32s3:
    entry   a1, 32
    srli    a5, a3, 2
    extui   a6, a3, 0, 2
    loopgtz a5, 2f
    l8ui    a8, a2, 0
    l8ui    a9, a2, 1
    l8ui    a10, a2, 2
    l8ui    a11, a2, 3
    add     a8, a8, a4
    add     a9, a9, a4
    add     a10, a10, a4
    add     a11, a11, a4
    l8ui    a8, a8, 0
    l8ui    a9, a9, 0
    l8ui    a10, a10, 0
    l8ui    a11, a11, 0
    s8i     a8, a2, 0
    s8i     a9, a2, 1
    s8i     a10, a2, 2
    s8i     a11, a2, 3
    addi    a2, a2, 4
2:
    loopgtz a6, 3f
    l8ui    a8, a2, 0
    add     a8, a8, a4
    l8ui    a8, a8, 0
    s8i     a8, a2, 0
    addi    a2, a2, 1
3:
    retw.n
