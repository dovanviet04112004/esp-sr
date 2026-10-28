/** Front end of the pitch network SwiftF0: three log band-magnitude spectra a hop of clean PCM
 * (KEHOACH 3.11).
 *  @ctx any | non-blocking | the caller's tables are read at every hop; one stream per instance
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define DSP_SPEC_SWIFT_FRONT_SPECTRA 3    // FFTs of 1024, 2048 and 512 points, in this order
#define DSP_SPEC_SWIFT_FRONT_BANDS 128    // log-spaced bands a spectrum
#define DSP_SPEC_SWIFT_FRONT_DELAY_HOPS 3 // a hop's frame comes out with the hop this many later

// One spectrum's tables, the pinned model's: its window, then its bands as runs of bins band after band.
typedef struct {
    const float *window;   // points values
    const uint16_t *first; // each band's first bin
    const uint16_t *count; // each band's bins
    const float *weights;  // every band's weights, band after band
} dsp_spec_swift_front_tables_t;

typedef struct dsp_spec_swift_front_s dsp_spec_swift_front_t;

/** Bytes a front end needs, its three FFTs and its sample history included.
 *  @ctx any | non-blocking
 */
size_t dsp_spec_swift_front_workspace_bytes(void);

/** Build a front end in mem over tables, one a spectrum, which must outlive it.
 *  @ctx task | non-blocking, dl_fft allocates once | caller owns mem and the tables
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_SIZE short mem or a band past the bins | FFT errors
 */
esp_err_t dsp_spec_swift_front_init(dsp_spec_swift_front_t **out,
                                    const dsp_spec_swift_front_tables_t tables[DSP_SPEC_SWIFT_FRONT_SPECTRA],
                                    void *mem, size_t bytes);

/** Forget every sample, as at the start of a stream.
 *  @ctx any | non-blocking
 */
void dsp_spec_swift_front_reset(dsp_spec_swift_front_t *front);

/** Take one hop of clean PCM; once the frame DELAY_HOPS back is whole, write its log band magnitudes,
 * spectrum after spectrum, BANDS each, and set ready.
 *  @ctx any | non-blocking | hop holds GEN_GRID_HOP_SAMPLES samples; caller owns out
 */
esp_err_t dsp_spec_swift_front_push(dsp_spec_swift_front_t *front, const int16_t *hop, float *out,
                                    bool *ready);

#ifdef __cplusplus
}
#endif
