/** Analysis and synthesis windows of the shared time grid (KEHOACH 3.1).
 *  @ctx any | non-blocking
 */
#pragma once

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Periodic square-root Hann of n points; its square overlap-adds to one at a hop of n / 2.
 *  @ctx any | non-blocking | caller owns w, n floats
 */
void dsp_spec_window_sqrt_hann(float *w, size_t n);

#ifdef __cplusplus
}
#endif
