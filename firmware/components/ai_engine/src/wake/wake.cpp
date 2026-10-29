#include <math.h>
#include <string.h>

#include "ai_engine.h"
#include "core/branch.hpp"
#include "core/espdl_net.hpp"
#include "core/model_image.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "gen_listen.h"
#include "storage_format.h"
#include "wake/score.h"

namespace {

const char *const TAG = "ai_wake";
constexpr const char *kEntry = "wake";
constexpr int kInt8Min = -128;
constexpr int kInt8Max = 127;
constexpr size_t kLayoutRank = 3; // esp-dl: (1, frames, bands) in, (1, frames, 1) out

struct Wake {
    bool ready;
    ai::Int8Tensor in, out;
    const float *mean, *deviation; // NORM entry: means, then deviations
    float inverse_step;            // 2^-exponent of the input
    size_t bands, step;            // step: frames a call
    size_t fed;                    // frames fed since load or reset
    uint16_t *recent;              // the scorer's ring, PSRAM
    uint16_t *scores;              // the last call's smoothed scores, step of them, PSRAM
    ai_engine_wake_scorer_t scorer;
};

ai::EspdlNet s_net;
Wake s;

// Half up as ESP-PPQ quantises the graph's input: lrintf rounds exact halves to even (KEHOACH 3.11).
int8_t on_grid(float x, float inverse_step)
{
    const float q = floorf(x * inverse_step + 0.5f);
    return static_cast<int8_t>(q < kInt8Min ? kInt8Min : (q > kInt8Max ? kInt8Max : q));
}

bool laid_out(const ai::Blob &norm)
{
    const ai::Int8Tensor &in = s.in, &out = s.out;
    return in.data != nullptr && out.data != nullptr && in.rank == kLayoutRank && out.rank == kLayoutRank &&
           in.dims[1] > 0 && out.dims[1] == in.dims[1] && out.dims[2] == 1 &&
           in.dims[2] == GEN_LISTEN_N_BANDS && in.dims[2] * 2 * sizeof(float) == norm.size;
}

} // namespace

namespace ai {

void wake_drop() noexcept
{
    s_net.release();
    heap_caps_free(s.recent);
    heap_caps_free(s.scores);
    s = Wake{};
}

esp_err_t wake_load() noexcept
{
    const Blob graph = image_find(kEntry, STORAGE_MODEL_KIND_ESPDL);
    const Blob norm = image_find(kEntry, STORAGE_MODEL_KIND_NORM);
    if (graph.data == nullptr || norm.data == nullptr) { return ESP_OK; }
    const size_t hops = image_wake_smooth_hops();
    const esp_err_t err = s_net.build(graph, kEntry);
    if (err != ESP_OK) { return err; }
    s.in = s_net.input();
    s.out = s_net.output();
    if (!laid_out(norm) || hops == 0) {
        ESP_LOGE(TAG,
                 "%s: input rank %u, output rank %u, %u norm bytes or %u smoothing hops do not fit its TCN",
                 kEntry, (unsigned)s.in.rank, (unsigned)s.out.rank, (unsigned)norm.size, (unsigned)hops);
        wake_drop();
        return ESP_ERR_INVALID_SIZE;
    }
    s.step = s.in.dims[1];
    s.bands = s.in.dims[2];
    s.mean = reinterpret_cast<const float *>(norm.data);
    s.deviation = s.mean + s.bands;
    s.recent = static_cast<uint16_t *>(heap_caps_calloc(hops, sizeof(uint16_t), MALLOC_CAP_SPIRAM));
    s.scores = static_cast<uint16_t *>(heap_caps_calloc(s.step, sizeof(uint16_t), MALLOC_CAP_SPIRAM));
    if (s.recent == nullptr || s.scores == nullptr) {
        ESP_LOGE(TAG, "%s: no memory for %u smoothing hops and %u scores", kEntry, (unsigned)hops,
                 (unsigned)s.step);
        wake_drop();
        return ESP_ERR_NO_MEM;
    }
    ai_engine_wake_scorer_init(&s.scorer, s.out.exponent, s.recent, hops);
    s.inverse_step = ldexpf(1.0f, -s.in.exponent);
    s.ready = true;
    ESP_LOGI(TAG, "%s: %u frames a call, scored %u behind, averaged over %u", kEntry, (unsigned)s.step,
             (unsigned)(s.step - 1), (unsigned)hops);
    return ESP_OK;
}

bool wake_ready() noexcept
{
    return s.ready;
}

} // namespace ai

esp_err_t ai_engine_wake_step(const float *log_mel, uint16_t *score_permille)
{
    if (log_mel == nullptr || score_permille == nullptr) { return ESP_ERR_INVALID_ARG; }
    if (!s.ready) { return ESP_ERR_INVALID_STATE; }
    const size_t row = s.fed % s.step;
    int8_t *frame = s.in.data + row * s.bands;
    for (size_t b = 0; b < s.bands; b++) {
        frame[b] = on_grid((log_mel[b] - s.mean[b]) / s.deviation[b], s.inverse_step);
    }
    s.fed++;
    if (row + 1 == s.step) {
        const esp_err_t err = s_net.step();
        if (err != ESP_OK) { return err; }
        for (size_t t = 0; t < s.step; t++) {
            s.scores[t] = ai_engine_wake_score(&s.scorer, s.out.data[t]);
        }
    }
    // The frame step - 1 calls back sits in the last call's scores at the slot after this frame's row.
    *score_permille = s.fed >= s.step ? s.scores[(row + 1) % s.step] : 0;
    return ESP_OK;
}

size_t ai_engine_wake_delay(void)
{
    return s.ready ? s.step - 1 : 0;
}

void ai_engine_wake_reset(void)
{
    if (!s.ready) { return; }
    s_net.reset();
    ai_engine_wake_scorer_reset(&s.scorer);
    memset(s.scores, 0, s.step * sizeof(s.scores[0]));
    s.fed = 0;
}
