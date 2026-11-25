#include <stdlib.h>

#include "ns_ulunas/ulunas.h"
#include "parity.h"

bool parity_ns_ulunas(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t blob, bins, gain;
    if (!parity_tensor(buf, len, "weights", &blob) || blob.dtype != GOLD_U8 ||
        !parity_tensor(buf, len, "bins", &bins) || bins.dtype != GOLD_F32 ||
        bins.dims[1] != 2 * AI_ENGINE_ULUNAS_BINS || !parity_tensor(buf, len, "gain", &gain) ||
        gain.dims[0] != bins.dims[0]) {
        return false;
    }
    ai_engine_ulunas_weights_t w;
    if (ai_engine_ulunas_weights(blob.data, blob.dims[0], &w) != ESP_OK) { return false; }
    const size_t hops = bins.dims[0];
    ai_engine_ulunas_state_t *st = malloc(sizeof(*st));
    float *got = malloc(hops * AI_ENGINE_ULUNAS_BINS * sizeof(float));
    const bool ok = st != NULL && got != NULL;
    if (ok) {
        ai_engine_ulunas_reset(st);
        const float *in = bins.data;
        for (size_t h = 0; h < hops; h++) {
            ai_engine_ulunas_step(&w, st, in + h * 2 * AI_ENGINE_ULUNAS_BINS,
                                  got + h * AI_ENGINE_ULUNAS_BINS);
        }
        parity_report("ns_ulunas", case_name, "gain", gain.data, got, hops * AI_ENGINE_ULUNAS_BINS);
    }
    free(st);
    free(got);
    return ok;
}
