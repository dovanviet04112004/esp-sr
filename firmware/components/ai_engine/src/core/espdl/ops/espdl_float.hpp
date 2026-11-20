/** The float32 steps of esp-dl that ai_engine's int8 norms repeat bit for bit (KEHOACH 3.14 step 2.5): its
 *  Newton square root here, the division and round half up into int8 as the chip runs them (espdl_chip.hpp).
 *  @ctx any | non-blocking | pure
 */
#pragma once

#include <cmath>
#include <cstdint>
#include <cstring>

#include "espdl_chip.hpp"

namespace ai {

constexpr float kNewtonStop = 0.00001f; // EN of esp-dl's dl_math.hpp

/** v's float32 bits.
 *  @ctx any | non-blocking
 */
inline int32_t bits_of(float v) noexcept
{
    int32_t bits;
    memcpy(&bits, &v, sizeof(bits));
    return bits;
}

/** esp-dl's dl::math::sqrt_newton: halving is exact, so its double 0.5 gives what float does, and its first
 * step divides x by itself, 1 exactly.
 *  @ctx any | non-blocking
 */
inline float sqrt_newton(float x) noexcept
{
    if (x == 0.0f) { return 0.0f; }
    float last = x;
    float result = (x + 1.0f) * 0.5f;
    while (fabsf(result - last) > kNewtonStop) {
        last = result;
        result = (last + divide(x, last)) * 0.5f;
    }
    return result;
}

} // namespace ai
