#include "core/espdl/espdl_stem_conv.hpp"

#include <array>
#include <cstdint>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "dl_module_base.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "fbs_model.hpp"

extern "C" {
void ai_engine_dw3x3_bias_row_s8_esp32s3(int8_t *out, const int8_t *const *rows, const int8_t *taps,
                                         const int32_t *run);
void ai_engine_spread_row_s8_esp32s3(int8_t *out, const int8_t *in, int pixels);
}

namespace ai {

namespace {

const char *TAG = "stem_conv";

constexpr int kRank = 4;
constexpr int kStep = 16;
constexpr int kWordBytes = 4;
constexpr size_t kAlignBytes = 16;
constexpr int kTaps = 9;
constexpr int kRing = 3;

enum Input { kX = 0, kTapsIn, kBias, kInputs };
enum Run { kRunBias = 3, kRunSize = 4 };

// Inputs as esp_ppq_patches.stem_conv_from writes them: x [1, T, F, 1], the taps [9][16] and bias [16] of the
// C channels padded with zeros; output [1, T, F, C].
class StemConv final : public dl::module::Module {
  public:
    StemConv(const char *name, int shift, int channels)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), shift_(shift), channels_(channels)
    {}

    ~StemConv() override
    {
        heap_caps_free(rows_);
    }

    StemConv(const StemConv &) = delete;
    StemConv &operator=(const StemConv &) = delete;

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        std::vector<int> shape = input_shapes[kX];
        if (shape.size() == kRank) { shape[kRank - 1] = channels_; }
        return {shape};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[kX]);
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        if (!built_ && !build(context, x, y)) { return; }
        const int8_t *taps = context->get_tensor(m_inputs_index[kTapsIn])->get_element_ptr<int8_t>();
        run_[kRunBias] =
            reinterpret_cast<int32_t>(context->get_tensor(m_inputs_index[kBias])->get_element_ptr());
        const int8_t *xs = x->get_element_ptr<int8_t>();
        int8_t *ys = y->get_element_ptr<int8_t>();
        memset(rows_, 0, kRing * padded_row_);
        spread(0, xs);
        const int words = channels_ / kWordBytes;
        for (int t = 0; t < frames_; t++) {
            spread(t + 1, xs);
            const int8_t *window[kRing] = {row(t - 1), row(t), row(t + 1)};
            ai_engine_dw3x3_bias_row_s8_esp32s3(convolved_, window, taps, run_.data());
            const uint32_t *from = reinterpret_cast<const uint32_t *>(convolved_);
            uint32_t *to = reinterpret_cast<uint32_t *>(ys + t * width_ * channels_);
            for (int f = 0; f < width_; f++) {
                for (int w = 0; w < words; w++) {
                    to[f * words + w] = from[f * (kStep / kWordBytes) + w];
                }
            }
        }
    }

    void print() override
    {
        ESP_LOGI(TAG, "shift: %d, quant_type: %s.", shift_, dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        int shift = 0;
        int channels = 0;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "shift", shift);
        fbs_model->get_operation_attribute(node_name, "channels", channels);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || shift < 1 || channels <= 0 || channels > kStep ||
            channels % kWordBytes != 0) {
            ESP_LOGE(TAG, "%s: only an int8 stem of shift 1 or more into up to 16 channels runs as StemConv",
                     node_name.c_str());
            return nullptr;
        }
        return new StemConv(node_name.c_str(), shift, channels);
    }

  private:
    int8_t *row(int t) const noexcept
    {
        return rows_ + ((t + kRing) % kRing) * padded_row_;
    }

    // Row t of x spread into its ring slot past the zero pixel at its start; zeros past the frames.
    void spread(int t, const int8_t *xs) noexcept
    {
        int8_t *slot = row(t) + kStep;
        if (t >= frames_) {
            memset(slot, 0, width_ * kStep);
        } else {
            ai_engine_spread_row_s8_esp32s3(slot, xs + t * width_, width_);
        }
    }

    bool build(dl::ModelContext *context, dl::TensorBase *x, dl::TensorBase *y)
    {
        dl::TensorBase *taps = context->get_tensor(m_inputs_index[kTapsIn]);
        dl::TensorBase *bias = context->get_tensor(m_inputs_index[kBias]);
        if (m_inputs_index.size() != kInputs || x->shape.size() != kRank || x->shape[3] != 1 ||
            y->shape.size() != kRank || y->shape[3] != channels_ || x->get_dtype() != dl::DATA_TYPE_INT8 ||
            y->get_dtype() != dl::DATA_TYPE_INT8 || taps->get_size() != kTaps * kStep ||
            bias->get_size() != kStep) {
            ESP_LOGE(TAG, "%s: not an int8 [1, T, F, 1] map into up to 16 channels", name);
            return false;
        }
        frames_ = x->shape[1];
        width_ = x->shape[2];
        padded_row_ = (width_ + 2) * kStep;
        bias->reset_bias_layout(quant_type, true);
        // A ring of three spread rows, then the convolved row.
        rows_ = static_cast<int8_t *>(heap_caps_aligned_calloc(
            kAlignBytes, kRing * padded_row_ + width_ * kStep, 1, MALLOC_CAP_SPIRAM));
        if (rows_ == nullptr) {
            ESP_LOGE(TAG, "%s: no memory for the spread rows", name);
            return false;
        }
        convolved_ = rows_ + kRing * padded_row_;
        run_ = {width_, 1, shift_, 0};
        built_ = true;
        return true;
    }

    int shift_;
    int channels_;
    int frames_ = 0;
    int width_ = 0;
    int padded_row_ = 0;
    int8_t *rows_ = nullptr;
    int8_t *convolved_ = nullptr;
    std::array<int32_t, kRunSize> run_ = {};
    bool built_ = false;
};

} // namespace

dl::module::Module *stem_conv_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return StemConv::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
