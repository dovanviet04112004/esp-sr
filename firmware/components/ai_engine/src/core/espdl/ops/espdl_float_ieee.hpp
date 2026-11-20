/** esp-dl's float division and round half up into int8 in standard C (KEHOACH 3.14, ADR-0045), for a chip
 * with no instructions of its own for them; make ai-kernels holds them to the S3's bits.
 *  @ctx any | non-blocking | pure
 */
#pragma once

#include <cmath>
#include <cstdint>

namespace ai::ieee {

/** a / b rounded once.
 *  @ctx any | non-blocking
 */
inline float divide(float a, float b) noexcept
{
    return a / b;
}

/** floor(v + 0.5) clipped into int8, the sum v + 0.5 rounded once.
 *  @ctx any | non-blocking
 */
inline int8_t round_half_up(float v) noexcept
{
    const float whole = floorf(v + 0.5f);
    return static_cast<int8_t>(whole > 127.0f ? 127 : whole < -128.0f ? -128 : static_cast<int>(whole));
}

} // namespace ai::ieee
