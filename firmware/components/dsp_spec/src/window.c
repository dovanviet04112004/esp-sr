#include <math.h>

#include "dsp_spec/window.h"

void dsp_spec_window_sqrt_hann(float *w, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        w[i] = (float)sin((double)((float)M_PI * (float)i / (float)n));
    }
}
