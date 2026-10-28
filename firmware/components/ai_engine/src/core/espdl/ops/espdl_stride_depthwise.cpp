#include "core/espdl/ops/espdl_stride_depthwise.hpp"

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
void ai_engine_dwk_row_s8_esp32s3(int8_t *out, const int8_t *const *rows, const int8_t *taps,
                                  const int32_t *run);
void ai_engine_pad_row_s8_esp32s3(int8_t *out, const int8_t *in, const int32_t *run);
}

namespace ai {

namespace {

const char *TAG = "stride_depthwise";

constexpr int kRank = 4;
constexpr int kStep = 16;
constexpr int kHalfStep = 8;
constexpr int kWordBytes = 4;
constexpr size_t kAlignBytes = 16;
constexpr int kMaxStride = 4;

enum Input { kX = 0, kTaps, kBias, kInputs };
enum Run { kRunBias = 3, kRunSize = 5 };

int padded(int channels) noexcept
{
    return (channels + kStep - 1) / kStep * kStep;
}

// Inputs as esp_ppq_patches.stride_depthwise_from writes them: x [1, T, F, C], the taps [Cp / 16][k][16] and
// the bias [Cp], Cp being C padded with zeros to a multiple of 16; output [1, T / k, F, C].
class StrideDepthwise final : public dl::module::Module {
  public:
    StrideDepthwise(const char *name, int shift, int stride)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), shift_(shift), stride_(stride)
    {}

    ~StrideDepthwise() override
    {
        heap_caps_free(rows_);
    }

    StrideDepthwise(const StrideDepthwise &) = delete;
    StrideDepthwise &operator=(const StrideDepthwise &) = delete;

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        std::vector<int> shape = input_shapes[kX];
        if (shape.size() == kRank) { shape[1] /= stride_; }
        return {shape};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[kX]);
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        if (!built_ && !build(context, x, y)) { return; }
        const int8_t *taps = context->get_tensor(m_inputs_index[kTaps])->get_element_ptr<int8_t>();
        run_[kRunBias] =
            reinterpret_cast<int32_t>(context->get_tensor(m_inputs_index[kBias])->get_element_ptr());
        const int8_t *xs = x->get_element_ptr<int8_t>();
        int8_t *ys = y->get_element_ptr<int8_t>();
        const int row_bytes = width_ * channels_;
        for (int t = 0; t < frames_; t++) {
            const int8_t *rows[kMaxStride] = {};
            for (int i = 0; i < stride_; i++) {
                const int8_t *in = xs + (t * stride_ + i) * row_bytes;
                if (padded_ == channels_) {
                    rows[i] = in;
                } else {
                    ai_engine_pad_row_s8_esp32s3(rows_ + i * width_ * padded_, in, pad_run_.data());
                    rows[i] = rows_ + i * width_ * padded_;
                }
            }
            int8_t *out = padded_ == channels_ ? ys + t * row_bytes : out_;
            ai_engine_dwk_row_s8_esp32s3(out, rows, taps, run_.data());
            if (out == out_) { compact(ys + t * row_bytes); }
        }
    }

    void print() override
    {
        ESP_LOGI(TAG, "shift: %d, stride: %d, quant_type: %s.", shift_, stride_,
                 dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        int shift = 0;
        int stride = 0;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "shift", shift);
        fbs_model->get_operation_attribute(node_name, "stride", stride);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || shift < 1 || (stride != 2 && stride != kMaxStride)) {
            ESP_LOGE(TAG, "%s: only an int8 stride of 2 or 4 shifting 1 or more runs as StrideDepthwise",
                     node_name.c_str());
            return nullptr;
        }
        return new StrideDepthwise(node_name.c_str(), shift, stride);
    }

  private:
    // The padded output row's C channels into y, a word at a time.
    void compact(int8_t *y) const noexcept
    {
        const uint32_t *from = reinterpret_cast<const uint32_t *>(out_);
        uint32_t *to = reinterpret_cast<uint32_t *>(y);
        const int words = channels_ / kWordBytes;
        const int stride_words = padded_ / kWordBytes;
        for (int f = 0; f < width_; f++) {
            for (int w = 0; w < words; w++) {
                to[f * words + w] = from[f * stride_words + w];
            }
        }
    }

    static bool aligned(const void *p) noexcept
    {
        return reinterpret_cast<uintptr_t>(p) % kAlignBytes == 0;
    }

    bool build(dl::ModelContext *context, dl::TensorBase *x, dl::TensorBase *y)
    {
        dl::TensorBase *taps = context->get_tensor(m_inputs_index[kTaps]);
        dl::TensorBase *bias = context->get_tensor(m_inputs_index[kBias]);
        const int c = x->shape.size() == kRank ? x->shape[3] : 0;
        if (m_inputs_index.size() != kInputs || c <= 0 || c % kHalfStep != 0 || y->shape.size() != kRank ||
            y->shape[3] != c || y->shape[2] != x->shape[2] || y->shape[1] * stride_ > x->shape[1] ||
            x->get_dtype() != dl::DATA_TYPE_INT8 || y->get_dtype() != dl::DATA_TYPE_INT8 ||
            taps->get_size() != stride_ * padded(c) || bias->get_size() != padded(c) ||
            !aligned(x->get_element_ptr())) {
            ESP_LOGE(TAG, "%s: not an int8 [1, T, F, C] map of C a multiple of 8 into T / %d frames", name,
                     stride_);
            return false;
        }
        frames_ = y->shape[1];
        width_ = x->shape[2];
        channels_ = c;
        padded_ = padded(c);
        bias->reset_bias_layout(quant_type, true);
        if (padded_ != channels_) {
            // stride padded input rows, then the padded output row.
            rows_ = static_cast<int8_t *>(
                heap_caps_aligned_calloc(kAlignBytes, (stride_ + 1) * width_, padded_, MALLOC_CAP_SPIRAM));
            if (rows_ == nullptr) {
                ESP_LOGE(TAG, "%s: no memory for the padded rows", name);
                return false;
            }
            out_ = rows_ + stride_ * width_ * padded_;
        }
        pad_run_ = {width_, channels_ / kStep, channels_ % kStep != 0};
        run_ = {width_, padded_ / kStep, shift_, 0, stride_};
        built_ = true;
        return true;
    }

    int shift_;
    int stride_;
    int frames_ = 0;
    int width_ = 0;
    int channels_ = 0;
    int padded_ = 0;
    int8_t *rows_ = nullptr;
    int8_t *out_ = nullptr;
    std::array<int32_t, 3> pad_run_ = {};
    std::array<int32_t, kRunSize> run_ = {};
    bool built_ = false;
};

} // namespace

dl::module::Module *stride_depthwise_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return StrideDepthwise::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
