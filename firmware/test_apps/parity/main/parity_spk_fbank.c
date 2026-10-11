#include <stdlib.h>

#include "dsp_spec.h"
#include "esp_heap_caps.h"
#include "parity.h"

enum {
    CONFIG_WINDOW = 0,
    CONFIG_FRAME,
    CONFIG_HOP,
    CONFIG_FFT,
    CONFIG_BANDS,
    CONFIG_F_MIN,
    CONFIG_F_MAX,
    CONFIG_PREEMPHASIS,
    CONFIG_EPS,
    CONFIG_COUNT
};

bool parity_spk_fbank(const char *case_name, const void *buf, size_t len)
{
    gold_tensor_t config, pcm, features;
    if (!parity_tensor(buf, len, "config", &config) || config.dims[0] != CONFIG_COUNT ||
        !parity_tensor(buf, len, "pcm", &pcm) || pcm.dtype != GOLD_I16 ||
        !parity_tensor(buf, len, "features", &features)) {
        return false;
    }
    const float *c = config.data;
    const dsp_spec_spk_fbank_config_t cfg = {
        .window_samples = (uint32_t)c[CONFIG_WINDOW],
        .frame_samples = (uint16_t)c[CONFIG_FRAME],
        .hop_samples = (uint16_t)c[CONFIG_HOP],
        .fft_size = (uint16_t)c[CONFIG_FFT],
        .n_bands = (uint16_t)c[CONFIG_BANDS],
        .f_min_hz = c[CONFIG_F_MIN],
        .f_max_hz = c[CONFIG_F_MAX],
        .preemphasis = c[CONFIG_PREEMPHASIS],
        .eps = c[CONFIG_EPS],
    };
    const size_t frames = dsp_spec_spk_fbank_frames(&cfg);
    if (frames == 0 || pcm.dims[0] != cfg.window_samples || features.dims[0] != frames ||
        features.dims[1] != cfg.n_bands) {
        return false;
    }
    const size_t bytes = dsp_spec_spk_fbank_workspace_bytes(&cfg);
    void *mem = malloc(bytes);
    float *got = heap_caps_malloc(frames * cfg.n_bands * sizeof(float), MALLOC_CAP_SPIRAM);
    dsp_spec_spk_fbank_t *fb = NULL;
    const bool ok = mem != NULL && got != NULL && dsp_spec_spk_fbank_init(&fb, &cfg, mem, bytes) == ESP_OK &&
                    dsp_spec_spk_fbank_run(fb, pcm.data, got) == ESP_OK;
    if (ok) { parity_report("spk_fbank", case_name, "features", features.data, got, frames * cfg.n_bands); }
    free(mem);
    free(got);
    return ok;
}
