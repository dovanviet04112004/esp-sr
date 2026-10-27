// Kernels of LayerNormS8 (KEHOACH 3.14 step 2.5): esp-dl's int8 LayerNormalization over the last axis, every float32
// step of layernorm_as_espdl rounded once as there, two positions a pass. n a multiple of 4 whose odd part is 1 or 3.
//
// void ai_engine_ln_deviations_s8_esp32s3(float *dev, float *variances, const int8_t *x, int pairs, const int32_t *run)
// run: n, n / 4, 128 n, then as float bits 1 / n, n, the variance's scale per value, epsilon, in_scale, 1e-5, 0.5.
// x: [2 pairs][n]. dev: [pairs][n][2], each value less its position's mean; variances: [2 pairs], epsilon plus the
// scale times the squares of dev summed in channel order, each step one fused multiply-add.
//
// void ai_engine_ln_scales_s8_esp32s3(float *scales, const float *variances, const int32_t *run, int pairs)
// scales: in_scale / sqrt_newton(variance), each division __divsf3's instructions of the S3 ROM; variances above 0.
//
// void ai_engine_ln_outputs_s8_esp32s3(int8_t *y, const float *dev, const float *scales, const float *gb, int pairs,
//                                      const int32_t *run)
// gb: [n][2] gamma and beta, both times the output's rescale, a power of 2. y: [2 pairs][n], each
// clamp(floor(fl(fl(fl(gamma * scale) * dev) + beta) + 0.5)).

    .text
    .align  4
    .global ai_engine_ln_deviations_s8_esp32s3
    .type   ai_engine_ln_deviations_s8_esp32s3, @function
    .global ai_engine_ln_scales_s8_esp32s3
    .type   ai_engine_ln_scales_s8_esp32s3, @function
    .global ai_engine_ln_outputs_s8_esp32s3
    .type   ai_engine_ln_outputs_s8_esp32s3, @function

ai_engine_ln_deviations_s8_esp32s3:
    entry   a1, 32
    l32i    a7, a6, 0
    l32i    a8, a6, 4
    l32i    a15, a6, 12
    wfr     f12, a15
    l32i    a15, a6, 16
    wfr     f13, a15
    l32i    a15, a6, 20
    wfr     f14, a15
    l32i    a15, a6, 24
    wfr     f15, a15
    movi    a9, 0x80
    slli    a14, a9, 8
    or      a9, a9, a14
    slli    a14, a9, 16
    or      a9, a9, a14
    movi    a10, 0xff
    slli    a14, a10, 16
    or      a10, a10, a14
1:
    // Sums of lane a, then lane b: each byte offset by 128 into two 16-bit halves of a word.
    movi    a12, 0
    mov     a13, a4
    loopgtz a8, 2f
    l32i    a14, a13, 0
    addi    a13, a13, 4
    xor     a14, a14, a9
    and     a15, a14, a10
    add     a12, a12, a15
    srli    a14, a14, 8
    and     a14, a14, a10
    add     a12, a12, a14
2:
    extui   a14, a12, 0, 16
    srli    a12, a12, 16
    add     a12, a12, a14
    l32i    a14, a6, 8
    sub     a12, a12, a14
    float.s f0, a12, 0
    movi    a12, 0
    loopgtz a8, 3f
    l32i    a14, a13, 0
    addi    a13, a13, 4
    xor     a14, a14, a9
    and     a15, a14, a10
    add     a12, a12, a15
    srli    a14, a14, 8
    and     a14, a14, a10
    add     a12, a12, a14
3:
    extui   a14, a12, 0, 16
    srli    a12, a12, 16
    add     a12, a12, a14
    l32i    a14, a6, 8
    sub     a12, a12, a14
    float.s f1, a12, 0
    // Means fma(fma(-q, n, s), 1 / n, q), q = s / n rounded; then deviations, squares summed in order.
    mul.s   f2, f0, f12
    mul.s   f3, f1, f12
    msub.s  f0, f2, f13
    msub.s  f1, f3, f13
    madd.s  f2, f0, f12
    madd.s  f3, f1, f12
    const.s f8, 0
    const.s f9, 0
    mov     a13, a4
    add     a11, a4, a7
    loopgtz a8, 4f
    l8ui    a12, a13, 0
    l8ui    a14, a11, 0
    l8ui    a15, a13, 1
    sext    a12, a12, 7
    sext    a14, a14, 7
    sext    a15, a15, 7
    float.s f0, a12, 0
    float.s f4, a14, 0
    float.s f1, a15, 0
    l8ui    a12, a11, 1
    l8ui    a14, a13, 2
    l8ui    a15, a11, 2
    sext    a12, a12, 7
    sext    a14, a14, 7
    sext    a15, a15, 7
    float.s f5, a12, 0
    float.s f6, a14, 0
    float.s f7, a15, 0
    sub.s   f0, f0, f2
    sub.s   f4, f4, f3
    sub.s   f1, f1, f2
    sub.s   f5, f5, f3
    l8ui    a12, a13, 3
    l8ui    a14, a11, 3
    sub.s   f6, f6, f2
    sub.s   f7, f7, f3
    sext    a12, a12, 7
    sext    a14, a14, 7
    float.s f10, a12, 0
    float.s f11, a14, 0
    madd.s  f8, f0, f0
    madd.s  f9, f4, f4
    ssi     f0, a2, 0
    ssi     f4, a2, 4
    ssi     f1, a2, 8
    ssi     f5, a2, 12
    sub.s   f10, f10, f2
    sub.s   f11, f11, f3
    ssi     f6, a2, 16
    ssi     f7, a2, 20
    madd.s  f8, f1, f1
    madd.s  f9, f5, f5
    ssi     f10, a2, 24
    ssi     f11, a2, 28
    addi    a13, a13, 4
    addi    a11, a11, 4
    madd.s  f8, f6, f6
    madd.s  f9, f7, f7
    addi    a2, a2, 32
    madd.s  f8, f10, f10
    madd.s  f9, f11, f11
4:
    mov.s   f0, f15
    mov.s   f1, f15
    madd.s  f0, f14, f8
    madd.s  f1, f14, f9
    ssi     f0, a3, 0
    ssi     f1, a3, 4
    addi    a3, a3, 8
    slli    a12, a7, 1
    add     a4, a4, a12
    addi    a5, a5, -1
    bnez    a5, 1b
    retw.n

    .align  4
ai_engine_ln_scales_s8_esp32s3:
    entry   a1, 32
    mov     a14, a5
    l32i    a13, a4, 28
    l32i    a10, a4, 32
    l32i    a9, a4, 36
1:
    l32i    a5, a3, 0
    l32i    a6, a3, 4
    wfr     f7, a5
    wfr     f15, a6
    const.s f5, 1
    const.s f13, 1
    add.s   f2, f7, f5
    add.s   f10, f15, f13
    wfr     f5, a9
    wfr     f13, a9
    mul.s   f2, f2, f5
    mul.s   f10, f10, f13
    sub.s   f6, f2, f7
    sub.s   f14, f10, f15
    abs.s   f6, f6
    abs.s   f14, f14
    wfr     f1, a10
    wfr     f9, a10
    olt.s   b0, f1, f6
    olt.s   b1, f9, f14
    rfr     a7, f2
    rfr     a8, f10
    // One Newton step a pass, lane 0 in f0-f7, lane 1 in f8-f15; a lane that has stopped keeps its root.
2:
    orb     b4, b0, b1
    bf      b4, 3f
    wfr     f0, a7
    wfr     f8, a8
    div0.s  f4, f0
    div0.s  f12, f8
    nexp01.s f1, f0
    nexp01.s f9, f8
    const.s f5, 1
    const.s f13, 1
    maddn.s f5, f1, f4
    maddn.s f13, f9, f12
    mov.s   f2, f4
    mov.s   f10, f12
    wfr     f7, a5
    wfr     f15, a6
    nexp01.s f7, f7
    nexp01.s f15, f15
    maddn.s f2, f5, f2
    maddn.s f10, f13, f10
    const.s f5, 1
    const.s f13, 1
    const.s f3, 0
    const.s f11, 0
    neg.s   f6, f7
    neg.s   f14, f15
    maddn.s f5, f1, f2
    maddn.s f13, f9, f10
    maddn.s f3, f6, f4
    maddn.s f11, f14, f12
    wfr     f4, a5
    wfr     f12, a6
    mkdadj.s f0, f4
    mkdadj.s f8, f12
    maddn.s f2, f5, f2
    maddn.s f10, f13, f10
    maddn.s f6, f1, f3
    maddn.s f14, f9, f11
    const.s f4, 1
    const.s f12, 1
    maddn.s f4, f1, f2
    maddn.s f12, f9, f10
    maddn.s f3, f6, f2
    maddn.s f11, f14, f10
    wfr     f7, a5
    wfr     f15, a6
    nexp01.s f7, f7
    nexp01.s f15, f15
    neg.s   f7, f7
    neg.s   f15, f15
    maddn.s f2, f4, f2
    maddn.s f10, f12, f10
    maddn.s f7, f1, f3
    maddn.s f15, f9, f11
    addexpm.s f3, f0
    addexpm.s f11, f8
    addexp.s f2, f0
    addexp.s f10, f8
    divn.s  f3, f7, f2
    divn.s  f11, f15, f10
    wfr     f1, a7
    wfr     f9, a8
    add.s   f2, f1, f3
    add.s   f10, f9, f11
    wfr     f5, a9
    wfr     f13, a9
    mul.s   f2, f2, f5
    mul.s   f10, f10, f13
    sub.s   f5, f2, f1
    sub.s   f13, f10, f9
    abs.s   f5, f5
    abs.s   f13, f13
    wfr     f1, a10
    wfr     f9, a10
    olt.s   b2, f1, f5
    olt.s   b3, f9, f13
    rfr     a11, f2
    rfr     a12, f10
    movt    a7, a11, b0
    movt    a8, a12, b1
    andb    b0, b0, b2
    andb    b1, b1, b3
    j       2b
3:
    // The inverse of each root, times in_scale.
    wfr     f0, a7
    wfr     f8, a8
    div0.s  f4, f0
    div0.s  f12, f8
    nexp01.s f1, f0
    nexp01.s f9, f8
    const.s f5, 1
    const.s f13, 1
    maddn.s f5, f1, f4
    maddn.s f13, f9, f12
    mov.s   f2, f4
    mov.s   f10, f12
    const.s f7, 1
    const.s f15, 1
    nexp01.s f7, f7
    nexp01.s f15, f15
    maddn.s f2, f5, f2
    maddn.s f10, f13, f10
    const.s f5, 1
    const.s f13, 1
    const.s f3, 0
    const.s f11, 0
    neg.s   f6, f7
    neg.s   f14, f15
    maddn.s f5, f1, f2
    maddn.s f13, f9, f10
    maddn.s f3, f6, f4
    maddn.s f11, f14, f12
    const.s f4, 1
    const.s f12, 1
    mkdadj.s f0, f4
    mkdadj.s f8, f12
    maddn.s f2, f5, f2
    maddn.s f10, f13, f10
    maddn.s f6, f1, f3
    maddn.s f14, f9, f11
    const.s f4, 1
    const.s f12, 1
    maddn.s f4, f1, f2
    maddn.s f12, f9, f10
    maddn.s f3, f6, f2
    maddn.s f11, f14, f10
    const.s f7, 1
    const.s f15, 1
    nexp01.s f7, f7
    nexp01.s f15, f15
    neg.s   f7, f7
    neg.s   f15, f15
    maddn.s f2, f4, f2
    maddn.s f10, f12, f10
    maddn.s f7, f1, f3
    maddn.s f15, f9, f11
    addexpm.s f3, f0
    addexpm.s f11, f8
    addexp.s f2, f0
    addexp.s f10, f8
    divn.s  f3, f7, f2
    divn.s  f11, f15, f10
    wfr     f1, a13
    wfr     f9, a13
    mul.s   f3, f3, f1
    mul.s   f11, f11, f9
    ssi     f3, a2, 0
    ssi     f11, a2, 4
    addi    a2, a2, 8
    addi    a3, a3, 8
    addi    a14, a14, -1
    bnez    a14, 1b
    retw.n

    .align  4
ai_engine_ln_outputs_s8_esp32s3:
    entry   a1, 32
    l32i    a7, a7, 0
    srli    a8, a7, 2
    movi    a9, 0x3f
    slli    a9, a9, 24
    wfr     f3, a9
1:
    lsi     f0, a4, 0
    lsi     f1, a4, 4
    addi    a4, a4, 8
    mov     a10, a5
    mov     a12, a2
    add     a13, a2, a7
    loopgtz a8, 2f
    lsi     f4, a10, 0
    lsi     f5, a10, 8
    lsi     f6, a10, 16
    lsi     f7, a10, 24
    mul.s   f8, f4, f0
    mul.s   f12, f4, f1
    mul.s   f9, f5, f0
    mul.s   f13, f5, f1
    lsi     f4, a3, 0
    mul.s   f10, f6, f0
    mul.s   f14, f6, f1
    lsi     f5, a3, 8
    mul.s   f11, f7, f0
    mul.s   f15, f7, f1
    lsi     f6, a3, 16
    lsi     f7, a3, 24
    mul.s   f8, f8, f4
    mul.s   f9, f9, f5
    lsi     f4, a3, 4
    mul.s   f10, f10, f6
    mul.s   f11, f11, f7
    lsi     f5, a3, 12
    mul.s   f12, f12, f4
    lsi     f6, a3, 20
    mul.s   f13, f13, f5
    lsi     f7, a3, 28
    mul.s   f14, f14, f6
    mul.s   f15, f15, f7
    lsi     f4, a10, 4
    lsi     f5, a10, 12
    add.s   f8, f8, f4
    lsi     f6, a10, 20
    add.s   f9, f9, f5
    lsi     f7, a10, 28
    add.s   f10, f10, f6
    add.s   f11, f11, f7
    add.s   f12, f12, f4
    add.s   f13, f13, f5
    add.s   f14, f14, f6
    add.s   f15, f15, f7
    add.s   f8, f8, f3
    add.s   f9, f9, f3
    add.s   f10, f10, f3
    add.s   f11, f11, f3
    add.s   f12, f12, f3
    add.s   f13, f13, f3
    add.s   f14, f14, f3
    add.s   f15, f15, f3
    floor.s a9, f8, 0
    floor.s a11, f9, 0
    floor.s a14, f10, 0
    floor.s a15, f11, 0
    clamps  a9, a9, 7
    clamps  a11, a11, 7
    clamps  a14, a14, 7
    clamps  a15, a15, 7
    s8i     a9, a12, 0
    s8i     a11, a12, 1
    s8i     a14, a12, 2
    s8i     a15, a12, 3
    floor.s a9, f12, 0
    floor.s a11, f13, 0
    floor.s a14, f14, 0
    floor.s a15, f15, 0
    clamps  a9, a9, 7
    clamps  a11, a11, 7
    clamps  a14, a14, 7
    clamps  a15, a15, 7
    s8i     a9, a13, 0
    s8i     a11, a13, 1
    s8i     a14, a13, 2
    s8i     a15, a13, 3
    addi    a10, a10, 32
    addi    a3, a3, 32
    addi    a12, a12, 4
    addi    a13, a13, 4
2:
    slli    a9, a7, 1
    add     a2, a2, a9
    addi    a6, a6, -1
    bnez    a6, 1b
    retw.n
