/** What ai_engine's ops take from the S3 (KEHOACH 3.14, ADR-0045): the dcache size they cut work by, and
 * esp-dl's float division and round half up into int8 as its S3 build runs them.
 *  @ctx any | non-blocking | pure
 */
#pragma once

#include <algorithm>
#include <cstdint>

#include "sdkconfig.h"

namespace ai {

constexpr int kDataCacheBytes = CONFIG_ESP32S3_DATA_CACHE_SIZE;

/** a / b by the instructions of the S3 ROM's __divsf3, in their order, without its call.
 *  @ctx any | non-blocking
 */
inline float divide(float a, float b) noexcept
{
    float q, t2, t3, t4, t5, t6, t7, t8;
    asm("div0.s %[t3], %[b]\n\t"
        "nexp01.s %[t4], %[b]\n\t"
        "const.s %[t5], 1\n\t"
        "maddn.s %[t5], %[t4], %[t3]\n\t"
        "mov.s %[t6], %[t3]\n\t"
        "mov.s %[t7], %[b]\n\t"
        "nexp01.s %[t2], %[a]\n\t"
        "maddn.s %[t6], %[t5], %[t6]\n\t"
        "const.s %[t5], 1\n\t"
        "const.s %[q], 0\n\t"
        "neg.s %[t8], %[t2]\n\t"
        "maddn.s %[t5], %[t4], %[t6]\n\t"
        "maddn.s %[q], %[t8], %[t3]\n\t"
        "mkdadj.s %[t7], %[a]\n\t"
        "maddn.s %[t6], %[t5], %[t6]\n\t"
        "maddn.s %[t8], %[t4], %[q]\n\t"
        "const.s %[t3], 1\n\t"
        "maddn.s %[t3], %[t4], %[t6]\n\t"
        "maddn.s %[q], %[t8], %[t6]\n\t"
        "neg.s %[t2], %[t2]\n\t"
        "maddn.s %[t6], %[t3], %[t6]\n\t"
        "maddn.s %[t2], %[t4], %[q]\n\t"
        "addexpm.s %[q], %[t7]\n\t"
        "addexp.s %[t6], %[t7]\n\t"
        "divn.s %[q], %[t2], %[t6]"
        : [q] "=&f"(q), [t2] "=&f"(t2), [t3] "=&f"(t3), [t4] "=&f"(t4), [t5] "=&f"(t5), [t6] "=&f"(t6),
          [t7] "=&f"(t7), [t8] "=&f"(t8)
        : [a] "f"(a), [b] "f"(b));
    return q;
}

/** v to int8 as esp-dl's tool::round does on the S3, floor(v + 0.5) in double then its int8 clip: below 2^22
 * the float sum v + 0.5 rounds to no other integer part, and the FPU's floor saturates beyond int.
 *  @ctx any | non-blocking
 */
inline int8_t round_to_s8(float v) noexcept
{
    int whole;
    asm("floor.s %0, %1, 0" : "=a"(whole) : "f"(v + 0.5f));
    return static_cast<int8_t>(
        std::max(std::min(whole, static_cast<int>(INT8_MAX)), static_cast<int>(INT8_MIN)));
}

/** A probability times its scale, v >= 0, to int8 as the S3's QuantizeLinear after a Softmax gives it: below
 * 2^22 the float sum v + 0.5 rounds to no other integer part, so truncating it floors it.
 *  @ctx any | non-blocking
 */
inline int8_t round_probability_to_s8(float v) noexcept
{
    return static_cast<int8_t>(std::min(static_cast<int>(v + 0.5f), static_cast<int>(INT8_MAX)));
}

/** value * 2^-shift to int8 as esp-dl's tool::shift_and_round does on the S3, half up, then its int8 clip.
 *  @ctx any | non-blocking
 */
inline int8_t shift_round_to_s8(int32_t value, int shift) noexcept
{
    int32_t shifted = shift > 0 ? (value + (1 << (shift - 1))) >> shift : value << -shift;
    return static_cast<int8_t>(
        std::max(std::min(shifted, static_cast<int32_t>(INT8_MAX)), static_cast<int32_t>(INT8_MIN)));
}

} // namespace ai
