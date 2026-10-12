#include <math.h>
#include <string.h>

#include "ai_engine.h"
#include "core/branch.hpp"
#include "core/espdl_net.hpp"
#include "core/model_image.hpp"
#include "dsp_spec/spk_fbank.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "gen_speaker.h"
#include "speaker/score.h"

namespace {

const char *const TAG = "ai_speaker";
constexpr const char *kEntry = "speaker";
constexpr const char *kCohortEntry = "speaker_cohort";
constexpr int kInt8Min = -128;
constexpr int kInt8Max = 127;
constexpr size_t kFeatures = GEN_SPEAKER_N_FRAMES * GEN_SPEAKER_N_BANDS;
static_assert(STORAGE_SPK_NOISE_EDGES == 3, "the refusal names three edges");

struct Speaker {
    bool ready;
    ai::Int8Tensor in, out;
    dsp_spec_spk_fbank_t *fbank;
    void *fbank_mem;
    float *features;     // frames x bands, frame after frame, PSRAM
    float inverse_step;  // 2^-exponent of the input
    const float *cohort; // in place in the mapped image, rows of the output's dims
    size_t cohort_rows;
    size_t cohort_top;
    float *cohort_scores;   // a vector's dot product with each row, PSRAM
    uint16_t *cohort_order; // its rows ranked, PSRAM
    int16_t thresholds[APP_SPEAKERS_MAX];
    uint32_t model_id;
};

ai::EspdlNet s_net;
Speaker s;

int8_t on_grid(float x, float inverse_step)
{
    const long q = lrintf(x * inverse_step);
    return static_cast<int8_t>(q < kInt8Min ? kInt8Min : (q > kInt8Max ? kInt8Max : q));
}

bool edges_hold(const float *edges)
{
    for (size_t k = 0; k < STORAGE_SPK_NOISE_EDGES; k++) {
        if (!isfinite(edges[k]) || edges[k] < 0.0f || (k > 0 && edges[k] < edges[k - 1])) { return false; }
    }
    return true;
}

} // namespace

namespace ai {

void speaker_drop() noexcept
{
    s_net.release();
    heap_caps_free(s.fbank_mem);
    heap_caps_free(s.features);
    heap_caps_free(s.cohort_scores);
    heap_caps_free(s.cohort_order);
    s = Speaker{};
}

esp_err_t speaker_load() noexcept
{
    const Blob graph = image_find(kEntry, STORAGE_MODEL_KIND_ESPDL);
    if (graph.data == nullptr || !image_speaks_as_built(kEntry)) { return ESP_OK; }
    const esp_err_t err = s_net.build(graph, kEntry);
    if (err != ESP_OK) { return err; }
    s.in = s_net.input();
    s.out = s_net.output();
    if (s.in.data == nullptr || s.out.data == nullptr || s.in.elements != kFeatures || s.out.elements == 0 ||
        s.out.elements > AI_ENGINE_SPEAKER_DIMS_MAX) {
        ESP_LOGE(TAG, "%s: input of %u values, %u wanted; output of %u, at most %d", kEntry,
                 (unsigned)s.in.elements, (unsigned)kFeatures, (unsigned)s.out.elements,
                 AI_ENGINE_SPEAKER_DIMS_MAX);
        speaker_drop();
        return ESP_ERR_INVALID_SIZE;
    }
    // Without its cohort, or a top within it, no voiceprint can be scored, so the branch stays off as with a
    // foreign hash (ADR-0023).
    const Blob cohort = image_find(kCohortEntry, STORAGE_MODEL_KIND_COHORT);
    const size_t row_bytes = s.out.elements * sizeof(float);
    const size_t rows = cohort.size / row_bytes;
    const size_t top = image_speaker_cohort_top();
    if (cohort.data == nullptr || rows == 0 || cohort.size % row_bytes != 0 || rows > UINT16_MAX ||
        top == 0 || top > rows) {
        ESP_LOGE(TAG, "%s: no cohort of %u-float rows with a top in it: %u bytes, top %u", kEntry,
                 (unsigned)s.out.elements, (unsigned)cohort.size, (unsigned)top);
        speaker_drop();
        return ESP_OK;
    }
    // Edges the chip would misread could pick a noisier bin's lower threshold, so they turn the branch away.
    const float *edges = image_speaker_noise_edges();
    if (!edges_hold(edges)) {
        ESP_LOGE(TAG, "%s: noise edges %g, %g, %g are not finite, at least 0 and rising (ADR-0024)", kEntry,
                 (double)edges[0], (double)edges[1], (double)edges[2]);
        speaker_drop();
        return ESP_OK;
    }
    s.cohort = reinterpret_cast<const float *>(cohort.data);
    s.cohort_rows = rows;
    s.cohort_top = top;
    const dsp_spec_spk_fbank_config_t cfg = GEN_SPEAKER_FBANK_CONFIG;
    const size_t bytes = dsp_spec_spk_fbank_workspace_bytes(&cfg);
    s.fbank_mem = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM);
    s.features = static_cast<float *>(heap_caps_malloc(kFeatures * sizeof(float), MALLOC_CAP_SPIRAM));
    s.cohort_scores = static_cast<float *>(heap_caps_malloc(rows * sizeof(float), MALLOC_CAP_SPIRAM));
    s.cohort_order = static_cast<uint16_t *>(heap_caps_malloc(rows * sizeof(uint16_t), MALLOC_CAP_SPIRAM));
    if (s.fbank_mem == nullptr || s.features == nullptr || s.cohort_scores == nullptr ||
        s.cohort_order == nullptr || dsp_spec_spk_fbank_init(&s.fbank, &cfg, s.fbank_mem, bytes) != ESP_OK) {
        ESP_LOGE(TAG, "%s: no front end, %u B of PSRAM asked", kEntry,
                 (unsigned)(bytes + kFeatures * sizeof(float) + rows * (sizeof(float) + sizeof(uint16_t))));
        speaker_drop();
        return ESP_ERR_NO_MEM;
    }
    s.inverse_step = ldexpf(1.0f, -s.in.exponent);
    for (size_t n = 1; n <= APP_SPEAKERS_MAX; n++) {
        s.thresholds[n - 1] = image_speaker_threshold_milli_z(n);
    }
    s.model_id = image_entry_id(kEntry, STORAGE_MODEL_KIND_ESPDL);
    s.ready = true;
    ESP_LOGI(
        TAG,
        "%s: %u frames x %u bands to %u dims, cohort of %u, top %u, threshold %d to %d thousandths for 1 "
        "to %d voiceprints, model %08x",
        kEntry, (unsigned)GEN_SPEAKER_N_FRAMES, (unsigned)GEN_SPEAKER_N_BANDS, (unsigned)s.out.elements,
        (unsigned)s.cohort_rows, (unsigned)s.cohort_top, s.thresholds[0], s.thresholds[APP_SPEAKERS_MAX - 1],
        APP_SPEAKERS_MAX, (unsigned)s.model_id);
    return ESP_OK;
}

bool speaker_ready() noexcept
{
    return s.ready;
}

} // namespace ai

esp_err_t ai_engine_speaker_embed(const int16_t *pcm, float *unit)
{
    if (pcm == nullptr || unit == nullptr) { return ESP_ERR_INVALID_ARG; }
    if (!s.ready) { return ESP_ERR_INVALID_STATE; }
    esp_err_t err = dsp_spec_spk_fbank_run(s.fbank, pcm, s.features);
    if (err != ESP_OK) { return err; }
    for (size_t i = 0; i < kFeatures; i++) {
        s.in.data[i] = on_grid(s.features[i], s.inverse_step);
    }
    err = s_net.step();
    if (err != ESP_OK) { return err; }
    ai_engine_speaker_unit(s.out.data, s.out.elements, s.out.exponent, unit);
    return ESP_OK;
}

size_t ai_engine_speaker_dims(void)
{
    return s.ready ? s.out.elements : 0;
}

esp_err_t ai_engine_speaker_norm(const float *voiceprint, float *mean, float *std)
{
    if (voiceprint == nullptr || mean == nullptr || std == nullptr) { return ESP_ERR_INVALID_ARG; }
    if (!s.ready) { return ESP_ERR_INVALID_STATE; }
    ai_engine_speaker_norm_of(voiceprint, s.cohort, s.cohort_rows, s.out.elements, s.cohort_top,
                              s.cohort_scores, s.cohort_order, mean, std);
    return ESP_OK;
}

int16_t ai_engine_speaker_best(const float *unit, const float *prints, const float *means, const float *stds,
                               size_t n, size_t *index, int16_t *cosine_permille)
{
    if (!s.ready) { n = 0; }
    float unit_mean = 0.0f;
    float unit_std = 1.0f;
    if (n > 0) { ai_engine_speaker_norm(unit, &unit_mean, &unit_std); }
    return ai_engine_speaker_best_as(unit, prints, means, stds, n, s.out.elements, unit_mean, unit_std, index,
                                     cosine_permille);
}

void ai_engine_speaker_voiceprint(const float *units, size_t n, float *voiceprint)
{
    ai_engine_speaker_fold(units, n, s.out.elements, voiceprint);
}

bool ai_engine_speaker_passes(int16_t milli_z, size_t prints)
{
    if (prints == 0 || prints > APP_SPEAKERS_MAX) { return false; }
    return ai_engine_speaker_accepts(milli_z, s.thresholds[prints - 1]);
}

uint32_t ai_engine_speaker_model_id(void)
{
    return s.ready ? s.model_id : 0;
}
