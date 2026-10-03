#include <math.h>

#include "ai_engine.h"
#include "command_ctc/ctc_score.h"
#include "core/branch.hpp"
#include "core/espdl_net.hpp"
#include "core/model_image.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "gen_listen.h"
#include "sdkconfig.h"
#include "storage_format.h"
#include "sys_storage.h"

namespace {

const char *const TAG = "ai_command";
// The image names a branch's entries after its backend (KEHOACH 6.3).
const char *const kEntry = "command_ctc";
constexpr long kInt8Min = -128;
constexpr long kInt8Max = 127;
constexpr size_t kLayoutRank = 3; // esp-dl: (1, features, hops) in, (1, frames, classes) out
constexpr uint16_t kFieldMax = UINT16_MAX;

struct Window {
    bool ready, open;
    ai::Int8Tensor in, out;
    const float *mean, *deviation; // NORM entry: means, then deviations
    int8_t *zero;                  // raw zeros on the input grid: the pad
    float *log_probs;              // frames x classes of the window, PSRAM
    void *work;                    // ai_engine_command_ctc_decide's, PSRAM
    float inverse_step;            // 2^-exponent of the input
    size_t features, chunk_hops, chunk_frames, classes, hops_max, per_frames;
    size_t hops, pending, frames;
    uint16_t reject, margin;
};

ai::EspdlNet s_net;
Window s;

int8_t on_grid(float x, float inverse_step)
{
    const long q = lrintf(x * inverse_step);
    return static_cast<int8_t>(q < kInt8Min ? kInt8Min : (q > kInt8Max ? kInt8Max : q));
}

esp_err_t run_chunk()
{
    const esp_err_t err = s_net.step();
    if (err != ESP_OK) { return err; }
    float *frames = s.log_probs + s.frames * s.classes;
    s.frames += s.chunk_frames;
    s.pending = 0;
    return ai_engine_command_ctc_log_probs(s.out.data, s.out.exponent, s.classes, s.chunk_frames, frames);
}

bool laid_out(const ai::Blob &norm)
{
    const ai::Int8Tensor &in = s.in, &out = s.out;
    return in.data != nullptr && out.data != nullptr && in.rank == kLayoutRank && out.rank == kLayoutRank &&
           in.dims[1] * 2 * sizeof(float) == norm.size && out.dims[1] > 0 && in.dims[2] % out.dims[1] == 0 &&
           out.dims[2] >= 2 && out.dims[2] <= AI_ENGINE_COMMAND_CTC_CLASSES_MAX;
}

// The chunk's hop h of features, a feature every chunk_hops int8 apart in esp-dl's (1, features, hops).
void put_hop(size_t h, const int8_t *hop)
{
    for (size_t d = 0; d < s.features; d++) {
        s.in.data[d * s.chunk_hops + h] = hop[d];
    }
}

} // namespace

namespace ai {

void command_drop() noexcept
{
    s_net.release();
    heap_caps_free(s.zero);
    heap_caps_free(s.log_probs);
    heap_caps_free(s.work);
    s = Window{};
}

esp_err_t command_load() noexcept
{
    const Blob graph = image_find(kEntry, STORAGE_MODEL_KIND_ESPDL);
    const Blob norm = image_find(kEntry, STORAGE_MODEL_KIND_NORM);
    if (graph.data == nullptr || norm.data == nullptr) { return ESP_OK; }
    uint16_t reject = 0, margin = 0;
    if (sys_storage_get_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_REJECT, &reject) != ESP_OK ||
        sys_storage_get_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN, &margin) != ESP_OK) {
        ESP_LOGE(TAG, "%s: NVS %s/%s or %s/%s absent, command off until seeded", kEntry, STORAGE_NS_KWS,
                 STORAGE_KEY_CMD_REJECT, STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN);
        return ESP_OK;
    }
    const esp_err_t err = s_net.build(graph, kEntry);
    if (err != ESP_OK) { return err; }
    s.in = s_net.input();
    s.out = s_net.output();
    if (!laid_out(norm)) {
        ESP_LOGE(TAG, "%s: input rank %u, output rank %u, or %u norm bytes do not fit a streaming ctc net",
                 kEntry, (unsigned)s.in.rank, (unsigned)s.out.rank, (unsigned)norm.size);
        command_drop();
        return ESP_ERR_INVALID_SIZE;
    }
    s.features = s.in.dims[1];
    if (s.features > AI_ENGINE_COMMAND_CTC_FEATURES_MAX) {
        ESP_LOGE(TAG, "%s: %u features a hop, at most %d", kEntry, (unsigned)s.features,
                 AI_ENGINE_COMMAND_CTC_FEATURES_MAX);
        command_drop();
        return ESP_ERR_INVALID_SIZE;
    }
    s.chunk_hops = s.in.dims[2];
    s.chunk_frames = s.out.dims[1];
    s.classes = s.out.dims[2];
    s.hops_max = GEN_LISTEN_WINDOW_HOPS;
    const size_t stride = s.chunk_hops / s.chunk_frames;
    s.per_frames = (s.hops_max + stride - 1) / stride;
    const size_t frames_max = (s.hops_max + s.chunk_hops - 1) / s.chunk_hops * s.chunk_frames;
    s.mean = reinterpret_cast<const float *>(norm.data);
    s.deviation = s.mean + s.features;
    s.inverse_step = ldexpf(1.0f, -s.in.exponent);
    s.zero = static_cast<int8_t *>(heap_caps_malloc(s.features, MALLOC_CAP_SPIRAM));
    s.log_probs =
        static_cast<float *>(heap_caps_malloc(frames_max * s.classes * sizeof(float), MALLOC_CAP_SPIRAM));
    s.work = heap_caps_malloc(ai_engine_command_ctc_work_bytes(s.classes, frames_max), MALLOC_CAP_SPIRAM);
    if (s.zero == nullptr || s.log_probs == nullptr || s.work == nullptr) {
        command_drop();
        return ESP_ERR_NO_MEM;
    }
    for (size_t d = 0; d < s.features; d++) {
        s.zero[d] = on_grid((0.0f - s.mean[d]) / s.deviation[d], s.inverse_step);
    }
    s.reject = reject;
    s.margin = margin;
    s.ready = true;
    ESP_LOGI(TAG, "%s: %u features, %u hops a chunk, %u frames of %u classes, %u hops at most", kEntry,
             (unsigned)s.features, (unsigned)s.chunk_hops, (unsigned)s.chunk_frames, (unsigned)s.classes,
             (unsigned)s.hops_max);
    return ESP_OK;
}

bool command_ready() noexcept
{
    return s.ready;
}

} // namespace ai

esp_err_t ai_engine_command_begin(void)
{
    if (!s.ready) { return ESP_ERR_INVALID_STATE; }
    // Empty caches, as training and the int8 simulation start every window (KEHOACH 3.12).
    s_net.reset();
    s.hops = 0;
    s.pending = 0;
    s.frames = 0;
    s.open = true;
    return ESP_OK;
}

esp_err_t ai_engine_command_step(const float *log_mel)
{
    if (log_mel == nullptr) { return ESP_ERR_INVALID_ARG; }
    if (!s.open) { return ESP_ERR_INVALID_STATE; }
    if (s.hops == s.hops_max) { return ESP_ERR_NO_MEM; }
    int8_t hop[AI_ENGINE_COMMAND_CTC_FEATURES_MAX];
    for (size_t d = 0; d < s.features; d++) {
        hop[d] = on_grid((log_mel[d] - s.mean[d]) / s.deviation[d], s.inverse_step);
    }
    put_hop(s.pending, hop);
    s.hops++;
    s.pending++;
    return s.pending == s.chunk_hops ? run_chunk() : ESP_OK;
}

esp_err_t ai_engine_command_score(const ai_engine_lexicon_t *lexicon, ai_engine_command_result_t *out)
{
    if (lexicon == nullptr || out == nullptr) { return ESP_ERR_INVALID_ARG; }
    if (!s.open) { return ESP_ERR_INVALID_STATE; }
    s.open = false;
    if (s.pending > 0) {
        for (size_t h = s.pending; h < s.chunk_hops; h++) {
            put_hop(h, s.zero);
        }
        const esp_err_t err = run_chunk();
        if (err != ESP_OK) { return err; }
    }
    const size_t stride = s.chunk_hops / s.chunk_frames;
    const size_t frames = (s.hops + stride - 1) / stride;
    if (frames == 0) {
        *out = ai_engine_command_result_t{.command = AI_ENGINE_COMMAND_CTC_REJECTED,
                                          .score_permille = 0,
                                          .margin_permille = kFieldMax,
                                          .free_gap_permille = kFieldMax};
        return ESP_OK;
    }
    return ai_engine_command_ctc_decide(s.log_probs, s.classes, frames, lexicon, s.per_frames, s.reject,
                                        s.margin, s.work, nullptr, out);
}
