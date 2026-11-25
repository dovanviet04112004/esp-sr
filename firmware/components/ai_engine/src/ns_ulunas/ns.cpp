#include "ai_engine.h"
#include "core/branch.hpp"
#include "core/model_image.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "ns_ulunas/ulunas.h"
#include "storage_format.h"

namespace {

const char *const TAG = "ai_ns";
constexpr const char *kEntry = "ns_ulunas";

// The net's state lives in PSRAM, so the slot's region in dsp_afe stays the floor's size (KEHOACH 6.5).
struct Net {
    bool ready;
    ai_engine_ulunas_weights_t w;
    ai_engine_ulunas_state_t *st;
};

Net s;

size_t state_bytes(void *ctx)
{
    (void)ctx;
    return sizeof(uint32_t);
}

esp_err_t init(void *ctx, void *state, size_t bytes)
{
    (void)ctx;
    if (state == nullptr || bytes < sizeof(uint32_t) || !s.ready) { return ESP_ERR_INVALID_ARG; }
    ai_engine_ulunas_reset(s.st);
    return ESP_OK;
}

void reset(void *ctx, void *state)
{
    (void)ctx;
    (void)state;
    ai_engine_ulunas_reset(s.st);
}

esp_err_t process(void *ctx, void *state, const float *bins, const float *echo_power, float *gain,
                  float *speech_prob)
{
    (void)ctx;
    (void)state;
    (void)echo_power;
    ai_engine_ulunas_step(&s.w, s.st, bins, gain);
    // The net has no head that tells speech from the rest.
    *speech_prob = 0.0f;
    return ESP_OK;
}

const dsp_afe_ns_ops_t kOps = {state_bytes, init, reset, process};

} // namespace

namespace ai {

void ns_drop() noexcept
{
    heap_caps_free(s.st);
    s = Net{};
}

esp_err_t ns_load() noexcept
{
    const Blob blob = image_find(kEntry, STORAGE_MODEL_KIND_WEIGHTS);
    if (blob.data == nullptr) { return ESP_OK; }
    Net net = {};
    const esp_err_t err = ai_engine_ulunas_weights(blob.data, blob.size, &net.w);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "%s refused (0x%x): the slot runs the floor", kEntry, (unsigned)err);
        return ESP_OK;
    }
    net.st = static_cast<ai_engine_ulunas_state_t *>(heap_caps_malloc(sizeof(*net.st), MALLOC_CAP_SPIRAM));
    if (net.st == nullptr) { return ESP_ERR_NO_MEM; }
    ai_engine_ulunas_reset(net.st);
    net.ready = true;
    s = net;
    return ESP_OK;
}

bool ns_ready() noexcept
{
    return s.ready;
}

} // namespace ai

const dsp_afe_ns_ops_t *ai_engine_ns_ops(void)
{
    return s.ready ? &kOps : nullptr;
}
