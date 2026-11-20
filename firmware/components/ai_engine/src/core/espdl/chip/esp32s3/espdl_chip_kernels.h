/** The integer steps of the S3's PIE that the plain C kernels repeat bit for bit (ADR-0045): QACC lanes of 20
 * bits saturating at every product, esp-dl's bias layout for them, tie728's round half up, and the saturating
 * adds.
 *  @ctx any | non-blocking | pure
 */
#pragma once

#include <math.h>
#include <stdint.h>

#define CHIP_QACC_BITS 20

/** v as a QACC lane holds it: saturated to 20 bits.
 *  @ctx any | non-blocking
 */
static inline int32_t chip_qacc(int64_t v)
{
    const int64_t most = (1 << (CHIP_QACC_BITS - 1)) - 1;
    return (int32_t)(v > most ? most : v < -most - 1 ? -most - 1 : v);
}

/** One product added to a lane: the lane saturates after each, so the kernels keep the PIE's order of
 * products.
 *  @ctx any | non-blocking
 */
static inline int32_t chip_mac(int32_t acc, int32_t a, int32_t b)
{
    return chip_qacc((int64_t)acc + a * b);
}

/** Lane 0..15's bias of a 64-byte block in reset_bias_layout's order: 8 lanes of 20 bits a 32-byte half.
 *  @ctx any | non-blocking
 */
static inline int32_t chip_bias(const int8_t *block, int lane)
{
    const uint8_t *half = (const uint8_t *)block + (lane / 8) * 32;
    const int bit = (lane % 8) * CHIP_QACC_BITS;
    const uint8_t *at = half + bit / 8;
    const uint32_t bits = ((uint32_t)at[0] | (uint32_t)at[1] << 8 | (uint32_t)at[2] << 16) >> (bit % 8);
    return (int32_t)(bits << (32 - CHIP_QACC_BITS)) >> (32 - CHIP_QACC_BITS);
}

static inline int8_t chip_sat8(int32_t v)
{
    return (int8_t)(v > INT8_MAX ? INT8_MAX : v < INT8_MIN ? INT8_MIN : v);
}

static inline int16_t chip_sat16(int32_t v)
{
    return (int16_t)(v > INT16_MAX ? INT16_MAX : v < INT16_MIN ? INT16_MIN : v);
}

/** A lane >> (less + 1) rounded half up, then saturated: srcmb by less, one added, srcmb by 1.
 *  @ctx any | non-blocking
 */
static inline int8_t chip_round(int32_t acc, int less)
{
    return chip_sat8(chip_qacc((int64_t)(acc >> less) + 1) >> 1);
}

/** v to int8 as esp-dl's tool::round does on the S3: floor of the float sum v + 0.5, clipped.
 *  @ctx any | non-blocking
 */
static inline int8_t chip_to_s8(float v)
{
    const float whole = floorf(v + 0.5f);
    return (int8_t)(whole > 127.0f ? 127 : whole < -128.0f ? -128 : (int)whole);
}

/** A probability times its scale, v >= 0, to int8 as softmax_s8_esp32s3.S gives it: v + 0.5 truncated, at
 * most 127.
 *  @ctx any | non-blocking
 */
static inline int8_t chip_probability_to_s8(float v)
{
    const int32_t whole = (int32_t)(v + 0.5f);
    return (int8_t)(whole < INT8_MAX ? whole : INT8_MAX);
}

static inline int8_t chip_adds8(int8_t a, int8_t b)
{
    return chip_sat8((int32_t)a + b);
}

static inline int8_t chip_subs8(int8_t a, int8_t b)
{
    return chip_sat8((int32_t)a - b);
}
