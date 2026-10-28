#include "core/espdl/espdl_stem_conv.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "core/espdl/espdl_layernorm_s8.hpp"
#include "core/espdl/espdl_loan.hpp"
#include "dl_define.hpp"
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
constexpr int kBiasBytes = 64; // 16 channels' bias in esp-dl's QACC layout

enum Input { kX = 0, kTapsIn, kBias, kInputs, kGamma = kInputs, kBeta };
enum Run { kRunBias = 3, kRunSize = 4 };

// Inputs as esp_ppq_patches.stem_conv_from writes them: x [1, T, F, 1], taps [V * 9][16], bias [V * 16], V =
// ceil(C / 16), padded with zeros; then the norm's gamma and beta when normed. Output [1, T, F, C].
class StemConv final : public dl::module::Module {
  public:
    StemConv(const char *name, int shift, int channels, bool normed, float epsilon, int conv_exponent)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), shift_(shift), channels_(channels),
          vectors_((channels + kStep - 1) / kStep), normed_(normed), epsilon_(epsilon),
          conv_exponent_(conv_exponent)
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
        const int8_t *bias = context->get_tensor(m_inputs_index[kBias])->get_element_ptr<int8_t>();
        const int8_t *xs = x->get_element_ptr<int8_t>();
        int8_t *ys = y->get_element_ptr<int8_t>();
        memset(rows_, 0, kRing * padded_row_);
        const Loan lent = loan_lent();
        const size_t row_bytes = static_cast<size_t>(width_) * channels_;
        int8_t *interleaved = lent.bytes >= row_bytes ? lent.mem : interleaved_;
        spread(0, xs);
        for (int t = 0; t < frames_; t++) {
            spread(t + 1, xs);
            const int8_t *window[kRing] = {row(t - 1), row(t), row(t + 1)};
            for (int v = 0; v < vectors_; v++) {
                run_[kRunBias] = reinterpret_cast<int32_t>(bias + v * kBiasBytes);
                ai_engine_dw3x3_bias_row_s8_esp32s3(convolved_ + v * width_ * kStep, window,
                                                    taps + v * kTaps * kStep, run_.data());
            }
            if (!normed_) {
                interleave(ys + t * width_ * channels_);
                continue;
            }
            interleave(interleaved);
            norm_.run(interleaved, ys + t * width_ * channels_, width_);
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
        int normed = 0;
        float epsilon = 1e-5f;
        int conv_exponent = 0;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "shift", shift);
        fbs_model->get_operation_attribute(node_name, "channels", channels);
        fbs_model->get_operation_attribute(node_name, "normed", normed);
        fbs_model->get_operation_attribute(node_name, "epsilon", epsilon);
        fbs_model->get_operation_attribute(node_name, "conv_exponent", conv_exponent);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || shift < 1 || channels <= 0 ||
            channels % kWordBytes != 0) {
            ESP_LOGE(
                TAG,
                "%s: only an int8 stem of shift 1 or more into a multiple of 4 channels runs as StemConv",
                node_name.c_str());
            return nullptr;
        }
        return new StemConv(node_name.c_str(), shift, channels, normed != 0, epsilon, conv_exponent);
    }

  private:
    int8_t *row(int t) const noexcept
    {
        return rows_ + ((t + kRing) % kRing) * padded_row_;
    }

    // The convolved row's 16-channel planes into C channels a pixel.
    void interleave(int8_t *out) const noexcept
    {
        uint32_t *to = reinterpret_cast<uint32_t *>(out);
        for (int f = 0; f < width_; f++) {
            for (int v = 0; v < vectors_; v++) {
                const uint32_t *from =
                    reinterpret_cast<const uint32_t *>(convolved_ + (v * width_ + f) * kStep);
                const int words = std::min(kStep, channels_ - v * kStep) / kWordBytes;
                for (int w = 0; w < words; w++) {
                    *to++ = from[w];
                }
            }
        }
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
        const size_t inputs = m_inputs_index.size();
        if ((normed_ ? inputs < kBeta || inputs > kBeta + 1 : inputs != kInputs) ||
            x->shape.size() != kRank || x->shape[3] != 1 || y->shape.size() != kRank ||
            y->shape[3] != channels_ || x->get_dtype() != dl::DATA_TYPE_INT8 ||
            y->get_dtype() != dl::DATA_TYPE_INT8 || taps->get_size() != vectors_ * kTaps * kStep ||
            bias->get_size() != vectors_ * kStep) {
            ESP_LOGE(TAG, "%s: not an int8 [1, T, F, 1] map into %d channels", name, channels_);
            return false;
        }
        frames_ = x->shape[1];
        width_ = x->shape[2];
        padded_row_ = (width_ + 2) * kStep;
        bias->reset_bias_layout(quant_type, true);
        // A ring of three spread rows, the convolved row's planes, then its interleaved row when normed.
        const int planes = vectors_ * width_ * kStep;
        rows_ = static_cast<int8_t *>(heap_caps_aligned_calloc(
            kAlignBytes, kRing * padded_row_ + planes + (normed_ ? width_ * channels_ : 0), 1,
            MALLOC_CAP_SPIRAM));
        if (rows_ == nullptr) {
            ESP_LOGE(TAG, "%s: no memory for the spread rows", name);
            return false;
        }
        convolved_ = rows_ + kRing * padded_row_;
        interleaved_ = convolved_ + planes;
        if (normed_ && !build_norm(context, y)) { return false; }
        run_ = {width_, 1, shift_, 0};
        built_ = true;
        return true;
    }

    bool build_norm(dl::ModelContext *context, dl::TensorBase *y)
    {
        dl::TensorBase *gamma = context->get_tensor(m_inputs_index[kGamma]);
        dl::TensorBase *beta =
            m_inputs_index.size() > kBeta ? context->get_tensor(m_inputs_index[kBeta]) : nullptr;
        if (gamma->get_size() != channels_ || (beta != nullptr && beta->get_size() != channels_)) {
            ESP_LOGE(TAG, "%s: the norm's gamma or beta is not %d values", name, channels_);
            return false;
        }
        if (!norm_.init(channels_, gamma->get_element_ptr<int8_t>(), DL_SCALE(gamma->get_exponent()),
                        beta != nullptr ? beta->get_element_ptr<int8_t>() : nullptr,
                        beta != nullptr ? DL_SCALE(beta->get_exponent()) : 0.0f, conv_exponent_,
                        y->get_exponent(), epsilon_)) {
            ESP_LOGE(TAG, "%s: no memory for the norm's floats", name);
            return false;
        }
        return true;
    }

    int shift_;
    int channels_;
    int vectors_; // 16-channel passes
    bool normed_;
    float epsilon_;
    int conv_exponent_; // the convolution's own grid, ahead of the norm
    int frames_ = 0;
    int width_ = 0;
    int padded_row_ = 0;
    int8_t *rows_ = nullptr;
    int8_t *convolved_ = nullptr;
    int8_t *interleaved_ = nullptr; // when the loan cannot hold the row
    LayerNormRows norm_;
    std::array<int32_t, kRunSize> run_ = {};
    bool built_ = false;
};

} // namespace

dl::module::Module *stem_conv_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return StemConv::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
