#include "core/espdl/ops/espdl_attention.hpp"

#include <array>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "core/espdl/ops/espdl_softmax_quant.hpp"
#include "dl_define.hpp"
#include "dl_module_base.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "fbs_model.hpp"

extern "C" {
void ai_engine_pw_bias_row_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                      const int32_t *run);
}

namespace ai {

namespace {

const char *TAG = "attention";

constexpr int kStep = 16;
constexpr int kHalfStep = 8;
constexpr size_t kAlignBytes = 16;

enum Input { kKeys = 0, kQueries, kValues, kProducts };
enum Run { kRunSize = 9 };

int round_up(int n, int step) noexcept
{
    return (n + step - 1) / step * step;
}

// The run of ai_engine_pw_bias_row_s8_esp32s3 for rows of `inputs` values, a row stride of `stride` bytes,
// one block of 16 outputs and no bias.
std::array<int32_t, kRunSize> product_run(int rows, int inputs, int stride, int shift) noexcept
{
    const int whole = inputs / kStep;
    const int half = inputs % kStep != 0;
    return {rows, 1, stride / kStep, half ? whole : whole - 1, half, shift, 0, 0, kStep};
}

// Inputs as esp_ppq_patches.attention_core_from writes them: keys, queries and values [1, T, H * D] as their
// projections give them, then the int8 products by byte of the scores' scale, if any; output [1, T, H * D].
class AttentionCore final : public dl::module::Module {
  public:
    AttentionCore(const char *name, int heads, int score_shift, int value_shift, int score_exponent,
                  int prob_exponent)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), heads_(heads),
          score_shift_(score_shift), value_shift_(value_shift), score_exponent_(score_exponent),
          prob_exponent_(prob_exponent)
    {}

    ~AttentionCore() override
    {
        heap_caps_free(table_);
        heap_caps_free(buffers_);
    }

    AttentionCore(const AttentionCore &) = delete;
    AttentionCore &operator=(const AttentionCore &) = delete;

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        return {input_shapes[kValues]};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *keys = context->get_tensor(m_inputs_index[kKeys]);
        dl::TensorBase *queries = context->get_tensor(m_inputs_index[kQueries]);
        dl::TensorBase *values = context->get_tensor(m_inputs_index[kValues]);
        dl::TensorBase *out = context->get_tensor(m_outputs_index[0]);
        if (!built_ && !build(context, keys, queries, values, out)) { return; }
        const int8_t *k = keys->get_element_ptr<int8_t>();
        const int8_t *q = queries->get_element_ptr<int8_t>();
        const int8_t *v = values->get_element_ptr<int8_t>();
        int8_t *o = out->get_element_ptr<int8_t>();
        for (int h = 0; h < heads_; h++) {
            lay_head(h, k, q, v);
            for (int g = 0; g < groups_; g++) {
                ai_engine_pw_bias_row_s8_esp32s3(scores_, keys_, queries_ + g * depth_ * kStep,
                                                 score_run_.data());
                softmax_block_s8(scores_, probs_, frames_, kStep, table_, scales_, out_rescale_);
                ai_engine_pw_bias_row_s8_esp32s3(weighted_, values_, probs_, value_run_.data());
                scatter(h, g, o);
            }
        }
    }

    void print() override
    {
        ESP_LOGI(TAG, "heads: %d, shifts: %d %d, quant_type: %s.", heads_, score_shift_, value_shift_,
                 dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        int heads = 0;
        int score_shift = 0;
        int value_shift = 0;
        int score_exponent = 0;
        int prob_exponent = 0;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "heads", heads);
        fbs_model->get_operation_attribute(node_name, "score_shift", score_shift);
        fbs_model->get_operation_attribute(node_name, "value_shift", value_shift);
        fbs_model->get_operation_attribute(node_name, "score_exponent", score_exponent);
        fbs_model->get_operation_attribute(node_name, "prob_exponent", prob_exponent);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || heads < 1 || score_shift < 1 || value_shift < 1) {
            ESP_LOGE(TAG, "%s: only int8 heads whose products shift 1 or more run as AttentionCore",
                     node_name.c_str());
            return nullptr;
        }
        return new AttentionCore(node_name.c_str(), heads, score_shift, value_shift, score_exponent,
                                 prob_exponent);
    }

  private:
    // Head h's keys as rows of its D values, its queries as columns of 16 frames, its values as rows of
    // frames.
    void lay_head(int h, const int8_t *k, const int8_t *q, const int8_t *v) noexcept
    {
        const int width = heads_ * dims_;
        for (int t = 0; t < frames_; t++) {
            const int8_t *row_k = k + t * width + h * dims_;
            const int8_t *row_q = q + t * width + h * dims_;
            const int8_t *row_v = v + t * width + h * dims_;
            int8_t *column = queries_ + (t / kStep) * depth_ * kStep + t % kStep;
            for (int d = 0; d < dims_; d++) {
                keys_[t * key_bytes_ + d] = row_k[d];
                column[d * kStep] = row_q[d];
                values_[d * padded_frames_ + t] = row_v[d];
            }
        }
    }

    void scatter(int h, int g, int8_t *o) const noexcept
    {
        const int width = heads_ * dims_;
        const int first = g * kStep;
        const int lanes = frames_ - first < kStep ? frames_ - first : kStep;
        for (int lane = 0; lane < lanes; lane++) {
            int8_t *row = o + (first + lane) * width + h * dims_;
            for (int d = 0; d < dims_; d++) {
                row[d] = weighted_[d * kStep + lane];
            }
        }
    }

    bool build(dl::ModelContext *context, dl::TensorBase *keys, dl::TensorBase *queries,
               dl::TensorBase *values, dl::TensorBase *out)
    {
        const int width = keys->shape.empty() ? 0 : keys->shape.back();
        if (keys->shape != queries->shape || keys->shape != values->shape || keys->shape != out->shape ||
            keys->shape.size() != 3 || keys->shape[0] != 1 || width % heads_ != 0 ||
            keys->get_dtype() != dl::DATA_TYPE_INT8 || out->get_dtype() != dl::DATA_TYPE_INT8) {
            ESP_LOGE(TAG, "%s: keys, queries, values and output not one int8 [1, T, H * D]", name);
            return false;
        }
        frames_ = keys->shape[1];
        dims_ = width / heads_;
        depth_ = dims_ <= kHalfStep ? kHalfStep : round_up(dims_, kStep);
        key_bytes_ = round_up(depth_, kStep);
        padded_frames_ = round_up(frames_, kStep);
        groups_ = padded_frames_ / kStep;
        const size_t sizes[] = {static_cast<size_t>(frames_) * key_bytes_,
                                static_cast<size_t>(groups_) * depth_ * kStep,
                                static_cast<size_t>(dims_) * padded_frames_,
                                static_cast<size_t>(frames_) * kStep,
                                static_cast<size_t>(padded_frames_) * kStep,
                                static_cast<size_t>(dims_) * kStep,
                                kStep * sizeof(float)};
        size_t total = 0;
        for (size_t size : sizes) {
            total += round_up(static_cast<int>(size), kStep);
        }
        buffers_ = static_cast<int8_t *>(heap_caps_aligned_calloc(kAlignBytes, 1, total, MALLOC_CAP_SPIRAM));
        table_ = static_cast<float *>(heap_caps_malloc(kSoftmaxTableSize * sizeof(float), MALLOC_CAP_SPIRAM));
        dl::TensorBase *products =
            m_inputs_index.size() > kProducts ? context->get_tensor(m_inputs_index[kProducts]) : nullptr;
        if (buffers_ == nullptr || table_ == nullptr ||
            !softmax_table_s8(table_, products ? products->get_exponent() : score_exponent_,
                              products ? products->get_element_ptr<int8_t>() : nullptr)) {
            ESP_LOGE(TAG, "%s: no memory for %u bytes of rows or the exp table", name,
                     static_cast<unsigned>(total));
            return false;
        }
        int8_t *next = buffers_;
        int8_t **slots[] = {&keys_, &queries_, &values_, &scores_, &probs_, &weighted_};
        for (size_t i = 0; i < sizeof(slots) / sizeof(slots[0]); i++) {
            *slots[i] = next;
            next += round_up(static_cast<int>(sizes[i]), kStep);
        }
        scales_ = reinterpret_cast<float *>(next);
        out_rescale_ = DL_RESCALE(prob_exponent_);
        score_run_ = product_run(frames_, depth_, key_bytes_, score_shift_);
        value_run_ = product_run(dims_, padded_frames_, padded_frames_, value_shift_);
        built_ = true;
        return true;
    }

    int heads_;
    int score_shift_;
    int value_shift_;
    int score_exponent_;
    int prob_exponent_;
    int frames_ = 0;
    int dims_ = 0;
    int depth_ = 0; // a head's D values padded to 8, or to 16 past 8
    int key_bytes_ = 0;
    int padded_frames_ = 0;
    int groups_ = 0;
    float out_rescale_ = 0.0f;
    float *table_ = nullptr;
    float *scales_ = nullptr;
    int8_t *buffers_ = nullptr;
    int8_t *keys_ = nullptr;     // [T][key_bytes_]
    int8_t *queries_ = nullptr;  // [groups][depth_][16]
    int8_t *values_ = nullptr;   // [D][padded frames]
    int8_t *scores_ = nullptr;   // [T][16] of one group
    int8_t *probs_ = nullptr;    // [padded frames][16]
    int8_t *weighted_ = nullptr; // [D][16]
    std::array<int32_t, kRunSize> score_run_ = {};
    std::array<int32_t, kRunSize> value_run_ = {};
    bool built_ = false;
};

} // namespace

dl::module::Module *attention_core_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return AttentionCore::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
