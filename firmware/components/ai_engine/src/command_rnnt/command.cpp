#include <math.h>
#include <string.h>

#include "ai_engine.h"
#include "command_ctc/ctc_score.h"
#include "command_rnnt/rnnt_search.h"
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
const char *const kFrames = "command_rnnt";
const char *const kPredictor = "rnnt_predictor";
const char *const kJoiner = "rnnt_joiner";
// The joiner's tensors by name, as rnnt/quant.py exports them.
const char *const kJoinFrame = "frame";
const char *const kJoinPrefix = "prefix";
const char *const kJoinLogits = "logits";
constexpr long kInt8Min = -128;
constexpr long kInt8Max = 127;
constexpr size_t kLayoutRank = 3; // esp-dl: (1, features, hops) in, (1, frames, width) out
constexpr size_t kContext = AI_ENGINE_COMMAND_RNNT_CONTEXT;

// The commands the tree holds, compared at each score: the frozen contract hands them over only there.
struct Built {
    uint8_t n_commands;
    uint8_t n_variants[AI_ENGINE_COMMANDS_MAX];
    uint8_t n_units[AI_ENGINE_COMMANDS_MAX][AI_ENGINE_VARIANTS_MAX];
    uint8_t units[AI_ENGINE_COMMANDS_MAX][AI_ENGINE_VARIANTS_MAX][AI_ENGINE_COMMAND_RNNT_UNITS_MAX];
};

struct Window {
    bool ready, open, built;
    ai::Int8Tensor in, out, hot, prefix_out, join_frame, join_prefix, join_out;
    const float *mean, *deviation; // NORM entry: means, then deviations
    int8_t *zero;                  // raw zeros on the input grid: the pad
    int8_t *projected;             // window frames x width, joiner frame grid, PSRAM
    int8_t *prefixes;              // (pad + 1)^2 contexts x width, joiner grid, PSRAM
    uint8_t *known;                // whether prefixes holds a context yet
    void *tree;                    // ai_engine_command_rnnt_build's area, PSRAM
    void *work;                    // ai_engine_command_rnnt_decide's, PSRAM
    Built *lexicon;                // the commands tree holds, PSRAM
    float inverse_step;            // 2^-exponent of the input
    int8_t one;                    // a one-hot 1 on the predictor's input grid
    bool hot_rows_first;           // predictor input (1, pad + 1, context), else swapped
    int frame_shift, prefix_shift; // source exponent less the joiner input's
    size_t features, chunk_hops, chunk_frames, width, classes, pad, hops_max;
    size_t hops, pending, frames;
    uint16_t reject, margin;
};

ai::EspdlNet s_frames, s_predictor, s_joiner;
Window s;

int8_t clamped(long q)
{
    return static_cast<int8_t>(q < kInt8Min ? kInt8Min : (q > kInt8Max ? kInt8Max : q));
}

int8_t on_grid(float x, float inverse_step)
{
    return clamped(lrintf(x * inverse_step));
}

// x * 2^shift held to int8, a shift down rounding half to even, as quant.py's requant.
int8_t requant(int8_t x, int shift)
{
    if (shift >= 8) { return clamped(x * (kInt8Max + 1)); }
    if (shift >= 0) { return clamped(static_cast<long>(x) * (1L << shift)); }
    if (shift <= -8) { return 0; }
    const long v = x;
    long q = v >> -shift;
    const long rest = v - q * (1L << -shift);
    const long half = 1L << (-shift - 1);
    if (rest > half || (rest == half && (q & 1) != 0)) { q++; }
    return clamped(q);
}

esp_err_t run_chunk()
{
    const esp_err_t err = s_frames.step();
    if (err != ESP_OK) { return err; }
    int8_t *frames = s.projected + s.frames * s.width;
    for (size_t i = 0; i < s.chunk_frames * s.width; i++) {
        frames[i] = requant(s.out.data[i], s.frame_shift);
    }
    s.frames += s.chunk_frames;
    s.pending = 0;
    return ESP_OK;
}

// The chunk's hop h of features, a feature every chunk_hops int8 apart in esp-dl's (1, features, hops).
void put_hop(size_t h, const int8_t *hop)
{
    for (size_t d = 0; d < s.features; d++) {
        s.in.data[d * s.chunk_hops + h] = hop[d];
    }
}

// The predictor's output for a context of classes, run once a context while the branch is loaded.
esp_err_t prefix_of(const uint8_t *context, const int8_t **prefix)
{
    size_t key = 0;
    for (size_t k = 0; k < kContext; k++) {
        if (context[k] > s.pad) { return ESP_ERR_INVALID_ARG; }
        key = key * (s.pad + 1) + context[k];
    }
    int8_t *slot = s.prefixes + key * s.width;
    if (!s.known[key]) {
        memset(s.hot.data, 0, s.hot.elements);
        for (size_t k = 0; k < kContext; k++) {
            const size_t at = s.hot_rows_first ? context[k] * kContext + k : k * (s.pad + 1) + context[k];
            s.hot.data[at] = s.one;
        }
        const esp_err_t err = s_predictor.step();
        if (err != ESP_OK) { return err; }
        for (size_t d = 0; d < s.width; d++) {
            slot[d] = requant(s.prefix_out.data[d], s.prefix_shift);
        }
        s.known[key] = 1;
    }
    *prefix = slot;
    return ESP_OK;
}

esp_err_t joined(void *ctx, size_t frame, const uint8_t *context, float *log_probs)
{
    (void)ctx;
    if (frame >= s.frames) { return ESP_ERR_INVALID_ARG; }
    const int8_t *prefix = nullptr;
    const esp_err_t err = prefix_of(context, &prefix);
    if (err != ESP_OK) { return err; }
    memcpy(s.join_frame.data, s.projected + frame * s.width, s.width);
    memcpy(s.join_prefix.data, prefix, s.width);
    const esp_err_t ran = s_joiner.step();
    if (ran != ESP_OK) { return ran; }
    return ai_engine_command_ctc_log_probs(s.join_out.data, s.join_out.exponent, s.classes, 1, log_probs);
}

bool same_lexicon(const ai_engine_lexicon_t *lexicon)
{
    const Built *b = s.lexicon;
    if (!s.built || b->n_commands != lexicon->n_commands) { return false; }
    for (size_t c = 0; c < lexicon->n_commands; c++) {
        if (b->n_variants[c] != lexicon->n_variants[c]) { return false; }
        for (size_t v = 0; v < lexicon->n_variants[c]; v++) {
            const ai_engine_seq_t &seq = lexicon->variants[c][v];
            if (b->n_units[c][v] != seq.n_units || memcmp(b->units[c][v], seq.units, seq.n_units) != 0) {
                return false;
            }
        }
    }
    return true;
}

esp_err_t build_tree(const ai_engine_lexicon_t *lexicon)
{
    s.built = false;
    if (lexicon->n_commands > AI_ENGINE_COMMANDS_MAX) { return ESP_ERR_INVALID_ARG; }
    for (size_t c = 0; c < lexicon->n_commands; c++) {
        if (lexicon->n_variants[c] > AI_ENGINE_VARIANTS_MAX) { return ESP_ERR_INVALID_ARG; }
    }
    const esp_err_t err = ai_engine_command_rnnt_build(lexicon, s.tree, ai_engine_command_rnnt_tree_bytes());
    if (err != ESP_OK) { return err; }
    Built *b = s.lexicon;
    b->n_commands = lexicon->n_commands;
    for (size_t c = 0; c < lexicon->n_commands; c++) {
        b->n_variants[c] = lexicon->n_variants[c];
        for (size_t v = 0; v < lexicon->n_variants[c]; v++) {
            b->n_units[c][v] = lexicon->variants[c][v].n_units;
            memcpy(b->units[c][v], lexicon->variants[c][v].units, lexicon->variants[c][v].n_units);
        }
    }
    s.built = true;
    return ESP_OK;
}

bool laid_out(const ai::Blob &norm)
{
    const ai::Int8Tensor &in = s.in, &out = s.out;
    const size_t hot_rows =
        s.hot.rank == kLayoutRank && s.hot.dims[2] == kContext ? s.hot.dims[1] : s.hot.dims[2];
    return in.data != nullptr && out.data != nullptr && in.rank == kLayoutRank && out.rank == kLayoutRank &&
           in.dims[1] * 2 * sizeof(float) == norm.size && out.dims[1] > 0 && in.dims[2] % out.dims[1] == 0 &&
           s.hot.data != nullptr && s.hot.elements == hot_rows * kContext && s.prefix_out.data != nullptr &&
           s.prefix_out.elements == out.dims[2] && s.join_frame.data != nullptr &&
           s.join_frame.elements == out.dims[2] && s.join_prefix.data != nullptr &&
           s.join_prefix.elements == out.dims[2] && s.join_out.data != nullptr && s.join_out.elements >= 2 &&
           s.join_out.elements + 1 == hot_rows && s.join_out.elements <= AI_ENGINE_COMMAND_CTC_CLASSES_MAX;
}

void *psram(size_t bytes)
{
    return heap_caps_calloc(1, bytes, MALLOC_CAP_SPIRAM);
}

} // namespace

namespace ai {

void command_drop() noexcept
{
    s_frames.release();
    s_predictor.release();
    s_joiner.release();
    heap_caps_free(s.zero);
    heap_caps_free(s.projected);
    heap_caps_free(s.prefixes);
    heap_caps_free(s.known);
    heap_caps_free(s.tree);
    heap_caps_free(s.work);
    heap_caps_free(s.lexicon);
    s = Window{};
}

esp_err_t command_load() noexcept
{
    const Blob frames = image_find(kFrames, STORAGE_MODEL_KIND_ESPDL);
    const Blob norm = image_find(kFrames, STORAGE_MODEL_KIND_NORM);
    const Blob predictor = image_find(kPredictor, STORAGE_MODEL_KIND_ESPDL);
    const Blob joiner = image_find(kJoiner, STORAGE_MODEL_KIND_ESPDL);
    if (frames.data == nullptr || norm.data == nullptr || predictor.data == nullptr ||
        joiner.data == nullptr) {
        return ESP_OK;
    }
    uint16_t reject = 0, margin = 0;
    if (sys_storage_get_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_REJECT, &reject) != ESP_OK ||
        sys_storage_get_u16(STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN, &margin) != ESP_OK) {
        ESP_LOGE(TAG, "%s: NVS %s/%s or %s/%s absent, command off until seeded", kFrames, STORAGE_NS_KWS,
                 STORAGE_KEY_CMD_REJECT, STORAGE_NS_KWS, STORAGE_KEY_CMD_MARGIN);
        return ESP_OK;
    }
    esp_err_t err = s_frames.build(frames, kFrames);
    if (err == ESP_OK) { err = s_predictor.build(predictor, kPredictor); }
    if (err == ESP_OK) { err = s_joiner.build(joiner, kJoiner); }
    if (err != ESP_OK) {
        command_drop();
        return err;
    }
    s.in = s_frames.input();
    s.out = s_frames.output();
    s.hot = s_predictor.input();
    s.prefix_out = s_predictor.output();
    s.join_frame = s_joiner.input(kJoinFrame);
    s.join_prefix = s_joiner.input(kJoinPrefix);
    s.join_out = s_joiner.output(kJoinLogits);
    if (!laid_out(norm)) {
        ESP_LOGE(TAG,
                 "%s: the frames, predictor or joiner graph do not fit a streaming rnnt of %u norm bytes",
                 kFrames, (unsigned)norm.size);
        command_drop();
        return ESP_ERR_INVALID_SIZE;
    }
    s.features = s.in.dims[1];
    if (s.features > AI_ENGINE_COMMAND_CTC_FEATURES_MAX) {
        ESP_LOGE(TAG, "%s: %u features a hop, at most %d", kFrames, (unsigned)s.features,
                 AI_ENGINE_COMMAND_CTC_FEATURES_MAX);
        command_drop();
        return ESP_ERR_INVALID_SIZE;
    }
    s.chunk_hops = s.in.dims[2];
    s.chunk_frames = s.out.dims[1];
    s.width = s.out.dims[2];
    s.classes = s.join_out.elements;
    s.pad = s.classes;
    s.hot_rows_first = s.hot.dims[2] == kContext;
    s.hops_max = GEN_LISTEN_WINDOW_HOPS;
    const long one = lrintf(ldexpf(1.0f, -s.hot.exponent));
    if (one < 1 || one > kInt8Max) {
        ESP_LOGE(TAG, "%s: a one-hot 1 is off the int8 grid of 2^%d", kPredictor, s.hot.exponent);
        command_drop();
        return ESP_ERR_INVALID_SIZE;
    }
    s.one = static_cast<int8_t>(one);
    s.frame_shift = s.out.exponent - s.join_frame.exponent;
    s.prefix_shift = s.prefix_out.exponent - s.join_prefix.exponent;
    const size_t frames_max = (s.hops_max + s.chunk_hops - 1) / s.chunk_hops * s.chunk_frames;
    const size_t contexts = (s.pad + 1) * (s.pad + 1);
    s.mean = reinterpret_cast<const float *>(norm.data);
    s.deviation = s.mean + s.features;
    s.inverse_step = ldexpf(1.0f, -s.in.exponent);
    s.zero = static_cast<int8_t *>(psram(s.features));
    s.projected = static_cast<int8_t *>(psram(frames_max * s.width));
    s.prefixes = static_cast<int8_t *>(psram(contexts * s.width));
    s.known = static_cast<uint8_t *>(psram(contexts));
    s.tree = psram(ai_engine_command_rnnt_tree_bytes());
    s.work = psram(ai_engine_command_rnnt_work_bytes(s.classes));
    s.lexicon = static_cast<Built *>(psram(sizeof(Built)));
    if (s.zero == nullptr || s.projected == nullptr || s.prefixes == nullptr || s.known == nullptr ||
        s.tree == nullptr || s.work == nullptr || s.lexicon == nullptr) {
        command_drop();
        return ESP_ERR_NO_MEM;
    }
    for (size_t d = 0; d < s.features; d++) {
        s.zero[d] = on_grid((0.0f - s.mean[d]) / s.deviation[d], s.inverse_step);
    }
    s.reject = reject;
    s.margin = margin;
    s.ready = true;
    ESP_LOGI(TAG, "%s: %u features, %u hops a chunk, %u frames of %u wide, %u classes, %u hops at most",
             kFrames, (unsigned)s.features, (unsigned)s.chunk_hops, (unsigned)s.chunk_frames,
             (unsigned)s.width, (unsigned)s.classes, (unsigned)s.hops_max);
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
    s_frames.reset();
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
    if (!same_lexicon(lexicon)) {
        const esp_err_t err = build_tree(lexicon);
        if (err != ESP_OK) { return err; }
    }
    const size_t stride = s.chunk_hops / s.chunk_frames;
    const size_t frames = (s.hops + stride - 1) / stride;
    return ai_engine_command_rnnt_decide(lexicon, s.tree, s.classes, frames, static_cast<uint8_t>(s.pad),
                                         joined, nullptr, s.reject, s.margin, s.work, nullptr, out);
}
