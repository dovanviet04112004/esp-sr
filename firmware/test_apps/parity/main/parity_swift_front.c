#include <stdio.h>
#include <stdlib.h>

#include "dsp_spec.h"
#include "esp_heap_caps.h"
#include "gen_grid.h"
#include "parity.h"

#define BANDS DSP_SPEC_SWIFT_FRONT_BANDS
#define FRAME_VALUES (DSP_SPEC_SWIFT_FRONT_SPECTRA * BANDS)

// Each spectrum's tables from the case: window and weights read in place, first and count bins copied to
// uint16.
static bool tables_of(const void *buf, size_t len, dsp_spec_swift_front_tables_t *tables, uint16_t *bins)
{
    for (unsigned s = 0; s < DSP_SPEC_SWIFT_FRONT_SPECTRA; s++) {
        char name[GOLD_NAME_BYTES];
        gold_tensor_t window, first, count, weights;
        snprintf(name, sizeof(name), "window_%u", s);
        bool ok = parity_tensor(buf, len, name, &window) && window.dtype == GOLD_F32;
        snprintf(name, sizeof(name), "first_%u", s);
        ok = ok && parity_tensor(buf, len, name, &first) && first.dims[0] == BANDS;
        snprintf(name, sizeof(name), "count_%u", s);
        ok = ok && parity_tensor(buf, len, name, &count) && count.dims[0] == BANDS;
        snprintf(name, sizeof(name), "weights_%u", s);
        ok = ok && parity_tensor(buf, len, name, &weights) && weights.dtype == GOLD_F32;
        float values[2 * BANDS];
        if (!ok || !parity_floats(&first, values, BANDS) || !parity_floats(&count, values + BANDS, BANDS)) {
            return false;
        }
        uint16_t *own = bins + s * 2 * BANDS;
        for (size_t i = 0; i < 2 * BANDS; i++) {
            own[i] = (uint16_t)values[i];
        }
        tables[s] = (dsp_spec_swift_front_tables_t){
            .window = window.data, .first = own, .count = own + BANDS, .weights = weights.data};
    }
    return true;
}

bool parity_swift_front(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t pcm, reset, out, ready;
    if (!parity_tensor(buf, len, "pcm", &pcm) || pcm.dtype != GOLD_I16 ||
        !parity_tensor(buf, len, "reset", &reset) || !parity_tensor(buf, len, "out", &out) ||
        !parity_tensor(buf, len, "ready", &ready)) {
        return false;
    }
    const size_t hops = reset.dims[0];
    if (pcm.dims[0] != hops * GEN_GRID_HOP_SAMPLES || out.dims[0] != hops ||
        out.dims[1] != DSP_SPEC_SWIFT_FRONT_SPECTRA || out.dims[2] != BANDS || ready.dims[0] != hops) {
        return false;
    }
    dsp_spec_swift_front_tables_t tables[DSP_SPEC_SWIFT_FRONT_SPECTRA];
    static uint16_t bins[DSP_SPEC_SWIFT_FRONT_SPECTRA * 2 * BANDS];
    const size_t bytes = dsp_spec_swift_front_workspace_bytes();
    void *mem = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM);
    float *flags = malloc(2 * hops * sizeof(float));
    float *got = heap_caps_calloc(hops * FRAME_VALUES, sizeof(float), MALLOC_CAP_SPIRAM);
    dsp_spec_swift_front_t *front = NULL;
    bool ok = mem != NULL && flags != NULL && got != NULL && tables_of(buf, len, tables, bins) &&
              parity_floats(&reset, flags, hops) && parity_floats(&ready, flags + hops, hops) &&
              dsp_spec_swift_front_init(&front, tables, mem, bytes) == ESP_OK;
    const int16_t *samples = pcm.data;
    for (size_t h = 0; ok && h < hops; h++) {
        if (flags[h] != 0.0f) { dsp_spec_swift_front_reset(front); }
        bool whole = false;
        ok = dsp_spec_swift_front_push(front, samples + h * GEN_GRID_HOP_SAMPLES, got + h * FRAME_VALUES,
                                       &whole) == ESP_OK &&
             whole == (flags[hops + h] != 0.0f);
    }
    if (ok) { parity_report("swift_front", case_name, "out", out.data, got, hops * FRAME_VALUES); }
    free(mem);
    free(flags);
    free(got);
    return ok;
}
