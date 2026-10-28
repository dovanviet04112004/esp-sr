// One term of StageSum over a row (KEHOACH 3.14 step 2.5): per 16 channels, esp-dl's int8 Mul of x by its
// channel weights, rounded onto the row's exponent as tie728_s8_vector_round_result rounds, then stored, or added
// to the row saturating as esp-dl's int8 Add does.
//
// void ai_engine_stage_sum_row_s8_esp32s3(int8_t *row, const int8_t *x, const int8_t *weights, const int32_t *run)
// run[0]: 16-byte vectors in the row; run[1]: the product's shift onto the row's exponent, 0 or more;
// run[2]: 1 stores the product, 0 adds it to the row.

    .text
    .align 4
    .global ai_engine_stage_sum_row_s8_esp32s3
    .type ai_engine_stage_sum_row_s8_esp32s3, @function
ai_engine_stage_sum_row_s8_esp32s3:
    entry sp, 32
    l32i a6, a5, 0
    l32i a7, a5, 4
    l32i a8, a5, 8

    // q7: every byte 1, the addend of the rounding; a10: shift - 1; a11: 1
    movi a9, 257
    slli a12, a9, 16
    or a9, a9, a12
    EE.MOVI.32.Q q7, a9, 0
    EE.MOVI.32.Q q7, a9, 1
    EE.MOVI.32.Q q7, a9, 2
    EE.MOVI.32.Q q7, a9, 3
    addi a10, a7, -1
    movi.n a11, 1
    mov a12, a2

    beqz a7, .Lunshifted
    beqz a8, .Ladd_rounded

.Lstore_rounded:
    loopgtz a6, .Lstore_rounded_end
        EE.VLD.128.IP q0, a3, 16
        EE.VLD.128.IP q1, a4, 16
        EE.ZERO.QACC
        EE.VMULAS.S8.QACC q0, q1
        EE.SRCMB.S8.QACC q2, a10, 0
        EE.MOVI.32.Q q2, a11, 0
        EE.VSMULAS.S8.QACC q7, q2, 0
        EE.SRCMB.S8.QACC q2, a11, 0
        EE.VST.128.IP q2, a2, 16
.Lstore_rounded_end:
    retw

.Ladd_rounded:
    loopgtz a6, .Ladd_rounded_end
        EE.VLD.128.IP q0, a3, 16
        EE.VLD.128.IP q1, a4, 16
        EE.ZERO.QACC
        EE.VMULAS.S8.QACC q0, q1
        EE.SRCMB.S8.QACC q2, a10, 0
        EE.MOVI.32.Q q2, a11, 0
        EE.VSMULAS.S8.QACC q7, q2, 0
        EE.SRCMB.S8.QACC q2, a11, 0
        EE.VLD.128.IP q3, a12, 16
        EE.VADDS.S8 q3, q3, q2
        EE.VST.128.IP q3, a2, 16
.Ladd_rounded_end:
    retw

.Lunshifted:
    beqz a8, .Ladd_unshifted
    loopgtz a6, .Lstore_unshifted_end
        EE.VLD.128.IP q0, a3, 16
        EE.VLD.128.IP q1, a4, 16
        EE.ZERO.QACC
        EE.VMULAS.S8.QACC q0, q1
        EE.SRCMB.S8.QACC q2, a7, 0
        EE.VST.128.IP q2, a2, 16
.Lstore_unshifted_end:
    retw

.Ladd_unshifted:
    loopgtz a6, .Ladd_unshifted_end
        EE.VLD.128.IP q0, a3, 16
        EE.VLD.128.IP q1, a4, 16
        EE.ZERO.QACC
        EE.VMULAS.S8.QACC q0, q1
        EE.SRCMB.S8.QACC q2, a7, 0
        EE.VLD.128.IP q3, a12, 16
        EE.VADDS.S8 q3, q3, q2
        EE.VST.128.IP q3, a2, 16
.Ladd_unshifted_end:
    retw
