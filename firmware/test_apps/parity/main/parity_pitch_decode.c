#include <stdint.h>
#include <stdlib.h>

#include "parity.h"
#include "pitch/decode.h"

enum { EXPONENT_LOGITS = 0, EXPONENT_SURE, EXPONENTS };
enum { RANGE_MIN = 0, RANGE_MAX, RANGE_ENDS };

bool parity_pitch_decode(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t logits, sure, exponents, range, log_f0, confidence;
    if (!parity_tensor(buf, len, "logits", &logits) || logits.dtype != GOLD_I8 ||
        !parity_tensor(buf, len, "sure", &sure) || sure.dtype != GOLD_I8 ||
        !parity_tensor(buf, len, "exponents", &exponents) || !parity_tensor(buf, len, "range_hz", &range) ||
        !parity_tensor(buf, len, "log_f0", &log_f0) || !parity_tensor(buf, len, "confidence", &confidence)) {
        return false;
    }
    const size_t frames = sure.dims[0];
    float exponent[EXPONENTS];
    float ends[RANGE_ENDS];
    if (logits.dims[0] != AI_ENGINE_PITCH_BINS || logits.dims[1] != frames || log_f0.dims[0] != frames ||
        confidence.dims[0] != frames || !parity_floats(&exponents, exponent, EXPONENTS) ||
        !parity_floats(&range, ends, RANGE_ENDS)) {
        return false;
    }
    static ai_engine_pitch_decoder_t decoder;
    float *got = malloc(2 * frames * sizeof(float));
    bool ok = got != NULL &&
              ai_engine_pitch_decoder_init(&decoder, ends[RANGE_MIN], ends[RANGE_MAX],
                                           (int)exponent[EXPONENT_LOGITS], (int)exponent[EXPONENT_SURE]);
    const int8_t *steps = logits.data;
    const int8_t *sures = sure.data;
    for (size_t t = 0; ok && t < frames; t++) {
        ai_engine_pitch_decode(&decoder, steps + t, frames, sures[t], got + t, got + frames + t);
    }
    if (ok) {
        parity_report("pitch_decode", case_name, "log_f0", log_f0.data, got, frames);
        parity_report("pitch_decode", case_name, "confidence", confidence.data, got + frames, frames);
    }
    free(got);
    return ok;
}
