#include <math.h>
#include <string.h>

#include "ai_engine.h"
#include "core/branch.hpp"
#include "core/espdl_net.hpp"
#include "core/model_image.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "gen_listen.h"
#include "pitch/decode.h"
#include "storage_format.h"

namespace {

const char *const TAG = "ai_pitch";
constexpr const char *kEntry = "pitch";
constexpr int kInt8Min = -128;
constexpr int kInt8Max = 127;
constexpr size_t kSpectra = DSP_SPEC_SWIFT_FRONT_SPECTRA;
constexpr size_t kBands = DSP_SPEC_SWIFT_FRONT_BANDS;
constexpr size_t kOutputs = 2; // the pitch logits and the confidence logit
constexpr uint32_t kPoints[kSpectra] = {1024, 2048, 512};

struct Pitch {
    bool ready;
    ai::Int8Tensor in;     // [1, bands, frames, spectra]
    ai::Int8Tensor logits; // [1, steps, frames]
    ai::Int8Tensor sure;   // [1, frames]
    size_t frames;
    size_t delay;       // frames the confidence trails the input
    size_t lead;        // frames the logits lead the confidence by
    int8_t *held;       // the last call's last lead logit columns, steps each
    float inverse_step; // 2^-exponent of the input
    dsp_spec_swift_front_tables_t tables[kSpectra];
    ai_engine_pitch_decoder_t decoder;
};

ai::EspdlNet s_net;
Pitch s;

int8_t on_grid(float x, float inverse_step)
{
    const long q = lrintf(x * inverse_step);
    return static_cast<int8_t>(q < kInt8Min ? kInt8Min : (q > kInt8Max ? kInt8Max : q));
}

size_t padded(size_t bytes)
{
    return (bytes + 3) / 4 * 4;
}

// The FRONT entry's tables in place (KEHOACH 6.3); false when its head or its size do not hold them.
bool tables_of(const ai::Blob &front, dsp_spec_swift_front_tables_t *tables)
{
    storage_model_front_t head;
    if (front.size < sizeof(head)) { return false; }
    memcpy(&head, front.data, sizeof(head));
    if (head.bands != kBands) { return false; }
    size_t at = sizeof(head);
    for (size_t k = 0; k < kSpectra; k++) {
        const size_t window = head.points[k] * sizeof(float);
        const size_t bins = kBands * sizeof(uint16_t);
        const size_t weights = head.weights[k] * sizeof(float);
        if (head.points[k] != kPoints[k] ||
            at + padded(window) + 2 * padded(bins) + padded(weights) > front.size) {
            return false;
        }
        const uint8_t *base = static_cast<const uint8_t *>(front.data);
        tables[k].window = reinterpret_cast<const float *>(base + at);
        at += padded(window);
        tables[k].first = reinterpret_cast<const uint16_t *>(base + at);
        at += padded(bins);
        tables[k].count = reinterpret_cast<const uint16_t *>(base + at);
        at += padded(bins);
        tables[k].weights = reinterpret_cast<const float *>(base + at);
        at += padded(weights);
    }
    return at == front.size;
}

// The logits [1, steps, frames] and the confidence [1, frames] among the graph's two outputs.
bool outputs_found()
{
    ai::Int8Tensor out[kOutputs];
    if (s_net.outputs(out, kOutputs) != kOutputs) { return false; }
    for (const ai::Int8Tensor &t : out) {
        if (t.data == nullptr) { return false; }
        if (t.rank == 3 && t.dims[1] == AI_ENGINE_PITCH_BINS) { s.logits = t; }
        if (t.rank == 2) { s.sure = t; }
    }
    return s.logits.data != nullptr && s.sure.data != nullptr && s.logits.dims[2] == s.frames &&
           s.sure.dims[1] == s.frames;
}

// The streaming graph's delays, as band_stream wrote them on its BandHead and its BandTail (KEHOACH 3.11).
bool delays_found()
{
    int logits = 0;
    int sure = 0;
    if (!s_net.node_int("delay", "bands", &logits) || !s_net.node_int("delay", "lag", &sure) || logits < 0 ||
        sure < logits || static_cast<size_t>(sure - logits) > s.frames) {
        return false;
    }
    s.delay = static_cast<size_t>(sure);
    s.lead = static_cast<size_t>(sure - logits);
    return true;
}

} // namespace

namespace ai {

void pitch_drop() noexcept
{
    s_net.release();
    heap_caps_free(s.held);
    s = Pitch{};
}

esp_err_t pitch_load() noexcept
{
    const Blob graph = image_find(kEntry, STORAGE_MODEL_KIND_ESPDL);
    const Blob front = image_find(kEntry, STORAGE_MODEL_KIND_FRONT);
    if (graph.data == nullptr || front.data == nullptr) { return ESP_OK; }
    if (!tables_of(front, s.tables)) {
        ESP_LOGE(TAG, "%s: a FRONT entry of %u bytes whose tables do not fit its head", kEntry,
                 (unsigned)front.size);
        return ESP_OK;
    }
    const esp_err_t err = s_net.build(graph, kEntry);
    if (err != ESP_OK) { return err; }
    s.in = s_net.input();
    s.frames = s.in.rank == 4 ? s.in.dims[2] : 0;
    if (s.in.data == nullptr || s.in.dims[1] != kBands || s.in.dims[3] != kSpectra || !outputs_found() ||
        !delays_found()) {
        ESP_LOGE(TAG, "%s: not SwiftF0's streaming graph: input, outputs or band_stream's delays", kEntry);
        pitch_drop();
        return ESP_ERR_INVALID_SIZE;
    }
    s.held = static_cast<int8_t *>(heap_caps_calloc(s.lead, AI_ENGINE_PITCH_BINS, MALLOC_CAP_SPIRAM));
    if (s.held == nullptr ||
        !ai_engine_pitch_decoder_init(&s.decoder, GEN_LISTEN_PITCH_MIN_F0_HZ, GEN_LISTEN_PITCH_MAX_F0_HZ,
                                      s.logits.exponent, s.sure.exponent)) {
        ESP_LOGE(TAG, "%s: no memory for its held logits, or no step within the F0 range", kEntry);
        pitch_drop();
        return ESP_ERR_NO_MEM;
    }
    s.inverse_step = ldexpf(1.0f, -s.in.exponent);
    s.ready = true;
    ESP_LOGI(TAG, "%s: %u frames a call, decoded %u behind", kEntry, (unsigned)s.frames, (unsigned)s.delay);
    return ESP_OK;
}

bool pitch_ready() noexcept
{
    return s.ready;
}

} // namespace ai

esp_err_t ai_engine_pitch_tables(dsp_spec_swift_front_tables_t tables[DSP_SPEC_SWIFT_FRONT_SPECTRA])
{
    if (!s.ready) { return ESP_ERR_INVALID_STATE; }
    if (tables == nullptr) { return ESP_ERR_INVALID_ARG; }
    memcpy(tables, s.tables, sizeof(s.tables));
    return ESP_OK;
}

size_t ai_engine_pitch_frames(void)
{
    return s.ready ? s.frames : 0;
}

size_t ai_engine_pitch_delay(void)
{
    return s.ready ? s.delay : 0;
}

esp_err_t ai_engine_pitch_run(const float *front, float *log_f0, float *confidence)
{
    if (!s.ready) { return ESP_ERR_INVALID_STATE; }
    if (front == nullptr || log_f0 == nullptr || confidence == nullptr) { return ESP_ERR_INVALID_ARG; }
    const size_t frames = s.frames;
    for (size_t t = 0; t < frames; t++) {
        for (size_t k = 0; k < kSpectra; k++) {
            const float *band = front + (t * kSpectra + k) * kBands;
            for (size_t b = 0; b < kBands; b++) {
                s.in.data[(b * frames + t) * kSpectra + k] = on_grid(band[b], s.inverse_step);
            }
        }
    }
    const esp_err_t err = s_net.step();
    if (err != ESP_OK) { return err; }
    for (size_t t = 0; t < frames; t++) {
        const bool held = t < s.lead;
        const int8_t *logits = held ? s.held + t * AI_ENGINE_PITCH_BINS : s.logits.data + (t - s.lead);
        ai_engine_pitch_decode(&s.decoder, logits, held ? 1 : frames, s.sure.data[t], &log_f0[t],
                               &confidence[t]);
    }
    for (size_t k = 0; k < s.lead; k++) {
        for (size_t i = 0; i < AI_ENGINE_PITCH_BINS; i++) {
            s.held[k * AI_ENGINE_PITCH_BINS + i] = s.logits.data[i * frames + frames - s.lead + k];
        }
    }
    return ESP_OK;
}

void ai_engine_pitch_reset(void)
{
    s_net.reset();
    if (s.held != nullptr) { memset(s.held, 0, s.lead * AI_ENGINE_PITCH_BINS); }
}
