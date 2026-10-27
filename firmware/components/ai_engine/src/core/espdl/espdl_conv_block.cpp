#include "core/espdl/espdl_conv_block.hpp"

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
void ai_engine_dw3x3_row_s8_esp32s3(int8_t *out, const int8_t *const *rows, const int8_t *taps,
                                    const int32_t *run);
void ai_engine_pad_row_s8_esp32s3(int8_t *out, const int8_t *in, const int32_t *run);
void ai_engine_pw_norm_row_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                      const int32_t *run);
void ai_engine_pw_residual_row_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                          const int32_t *run);
}

namespace ai {

namespace {

const char *TAG = "conv_block";

constexpr int kRank = 4;
constexpr int kChannelStep = 16;
constexpr int kHalfStep = 8;
constexpr size_t kVectorAlignBytes = 16;
constexpr int kTaps = 9;
constexpr int kShifts = 6;
constexpr int kRing = 3;
// Two rings of three padded rows, then the rows the first and second depthwise steps write.
constexpr int kRows = 2 * kRing + 2;

enum Input { kX = 0, kDw1, kPw1, kPw1Bias, kBn, kBnBias, kDw2, kPw2, kPw2Bias, kInputs };
enum Shift { kShiftDw1 = 0, kShiftPw1, kShiftBn, kShiftDw2, kShiftPw2, kShiftResidual };
// Slots of conv_row_s8_esp32s3.S's runs that hold addresses.
enum NormRun { kNormBias = 6, kNormWeights, kNormBnBias, kNormSize = 10 };
enum ResidualRun { kResidualBias = 6, kResidualX, kResidualSize = 10 };

int padded(int channels) noexcept
{
    return (channels + kChannelStep - 1) / kChannelStep * kChannelStep;
}

// Inputs as esp_ppq_patches.conv_block_from lays them, in Input's order: x [1, T, F, C], taps [9][Cp],
// pointwise [Cp / 16][C][16], bias and norm weights [Cp]; Cp is C padded with zeros to a multiple of 16.
class ConvBlock final : public dl::module::Module {
  public:
    ConvBlock(const char *name, std::vector<int> shifts)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), shifts_(std::move(shifts))
    {}

    ~ConvBlock() override
    {
        heap_caps_free(rows_);
    }

    ConvBlock(const ConvBlock &) = delete;
    ConvBlock &operator=(const ConvBlock &) = delete;

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        return {input_shapes[kX]};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[kX]);
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        if (!built_ && !build(context, x, y)) { return; }
        const int8_t *dw1 = context->get_tensor(m_inputs_index[kDw1])->get_element_ptr<int8_t>();
        const int8_t *pw1 = context->get_tensor(m_inputs_index[kPw1])->get_element_ptr<int8_t>();
        const int8_t *dw2 = context->get_tensor(m_inputs_index[kDw2])->get_element_ptr<int8_t>();
        const int8_t *pw2 = context->get_tensor(m_inputs_index[kPw2])->get_element_ptr<int8_t>();
        norm_run_[kNormBias] = address_of(context, kPw1Bias);
        norm_run_[kNormWeights] = address_of(context, kBn);
        norm_run_[kNormBnBias] = address_of(context, kBnBias);
        residual_run_[kResidualBias] = address_of(context, kPw2Bias);
        const int8_t *xs = x->get_element_ptr<int8_t>();
        int8_t *ys = y->get_element_ptr<int8_t>();
        memset(rows_, 0, 2 * kRing * padded_row_);
        load_x(0, xs);
        load_x(1, xs);
        norm_row(0, dw1, pw1);
        for (int t = 0; t < frames_; t++) {
            load_x(t + 2, xs);
            norm_row(t + 1, dw1, pw1);
            const int8_t *window[kRing] = {c_row(t - 1), c_row(t), c_row(t + 1)};
            ai_engine_dw3x3_row_s8_esp32s3(d_, window, dw2, dw2_run_.data());
            residual_run_[kResidualX] = reinterpret_cast<int32_t>(x_row(t) + padded_);
            ai_engine_pw_residual_row_s8_esp32s3(ys + t * width_ * channels_, d_, pw2, residual_run_.data());
        }
    }

    void print() override
    {
        ESP_LOGI(TAG, "shifts: %s, quant_type: %s.", dl::vector_to_string(shifts_).c_str(),
                 dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        std::vector<int> shifts;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "shifts", shifts);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || shifts.size() != kShifts) {
            ESP_LOGE(TAG, "%s: only an int8 block with six shifts runs as ConvBlock", node_name.c_str());
            return nullptr;
        }
        return new ConvBlock(node_name.c_str(), std::move(shifts));
    }

  private:
    int8_t *x_row(int t) const noexcept
    {
        return x_ring_ + ((t + kRing) % kRing) * padded_row_;
    }

    int8_t *c_row(int t) const noexcept
    {
        return c_ring_ + ((t + 1) % kRing) * padded_row_;
    }

    int32_t address_of(dl::ModelContext *context, int input) const noexcept
    {
        return reinterpret_cast<int32_t>(context->get_tensor(m_inputs_index[input])->get_element_ptr());
    }

    // Row t of x into its ring slot, a zero pixel at each end; zeros past the frames.
    void load_x(int t, const int8_t *xs) noexcept
    {
        int8_t *slot = x_row(t) + padded_;
        if (t >= frames_) {
            memset(slot, 0, width_ * padded_);
        } else if (channels_ == padded_) {
            memcpy(slot, xs + t * width_ * channels_, width_ * channels_);
        } else {
            ai_engine_pad_row_s8_esp32s3(slot, xs + t * width_ * channels_, pad_run_.data());
        }
    }

    // The norm's output at row t, from the first depthwise and pointwise; zeros past the frames, as padding
    // is.
    void norm_row(int t, const int8_t *dw1, const int8_t *pw1) noexcept
    {
        int8_t *slot = c_row(t) + padded_;
        if (t >= frames_) {
            memset(slot, 0, width_ * padded_);
            return;
        }
        const int8_t *window[kRing] = {x_row(t - 1), x_row(t), x_row(t + 1)};
        ai_engine_dw3x3_row_s8_esp32s3(a_, window, dw1, dw1_run_.data());
        ai_engine_pw_norm_row_s8_esp32s3(slot, a_, pw1, norm_run_.data());
    }

    static bool aligned(const void *p) noexcept
    {
        return reinterpret_cast<uintptr_t>(p) % kVectorAlignBytes == 0;
    }

    bool shapes_fit(dl::ModelContext *context, dl::TensorBase *x, dl::TensorBase *y) const
    {
        if (m_inputs_index.size() != kInputs || x->shape.size() != kRank || x->shape != y->shape ||
            x->get_dtype() != dl::DATA_TYPE_INT8 || y->get_dtype() != dl::DATA_TYPE_INT8 ||
            x->shape[3] % kHalfStep != 0) {
            ESP_LOGE(TAG, "%s: %u inputs, or x and y not one int8 [N, T, F, C] of C a multiple of 8", name,
                     static_cast<unsigned>(m_inputs_index.size()));
            return false;
        }
        const int c = x->shape[3];
        const int cp = padded(c);
        const int sizes[kInputs] = {0, kTaps * cp, cp * c, cp, cp, cp, kTaps * cp, cp * c, cp};
        for (int i = kDw1; i < kInputs; i++) {
            dl::TensorBase *p = context->get_tensor(m_inputs_index[i]);
            if (p->get_size() != sizes[i] || !aligned(p->get_element_ptr())) {
                ESP_LOGE(TAG, "%s: input %d holds %d values, the block wants %d", name, i, p->get_size(),
                         sizes[i]);
                return false;
            }
        }
        for (int k = kShiftDw1; k <= kShiftResidual; k++) {
            if (shifts_[k] < (k == kShiftResidual ? 0 : 1)) {
                ESP_LOGE(TAG, "%s: shift %d is %d, the kernels round right shifts of 1 or more", name, k,
                         shifts_[k]);
                return false;
            }
        }
        return true;
    }

    bool build(dl::ModelContext *context, dl::TensorBase *x, dl::TensorBase *y)
    {
        if (!shapes_fit(context, x, y)) { return false; }
        frames_ = x->shape[1];
        width_ = x->shape[2];
        channels_ = x->shape[3];
        padded_ = padded(channels_);
        padded_row_ = (width_ + 2) * padded_;
        for (int i : {kPw1Bias, kBnBias, kPw2Bias}) {
            context->get_tensor(m_inputs_index[i])->reset_bias_layout(quant_type, true);
        }
        rows_ = static_cast<int8_t *>(
            heap_caps_aligned_calloc(kVectorAlignBytes, kRows, padded_row_, MALLOC_CAP_SPIRAM));
        if (rows_ == nullptr) {
            ESP_LOGE(TAG, "%s: no memory for %d rows of %d bytes", name, kRows, padded_row_);
            return false;
        }
        x_ring_ = rows_;
        c_ring_ = x_ring_ + kRing * padded_row_;
        a_ = c_ring_ + kRing * padded_row_;
        d_ = a_ + padded_row_;
        const int vectors = padded_ / kChannelStep;
        const int whole = channels_ / kChannelStep;
        const int half = channels_ % kChannelStep != 0;
        // Whole input vectors whose last product loads the next; the tail then sums the last 16, or the 8
        // left.
        const int ahead = half ? whole : whole - 1;
        dw1_run_ = {width_, vectors, shifts_[kShiftDw1]};
        dw2_run_ = {width_, vectors, shifts_[kShiftDw2]};
        pad_run_ = {width_, whole, half};
        norm_run_ = {width_, vectors, vectors, ahead, half, shifts_[kShiftPw1], 0, 0, 0, shifts_[kShiftBn]};
        residual_run_ = {width_,
                         vectors,
                         vectors,
                         ahead,
                         half,
                         shifts_[kShiftPw2],
                         0,
                         0,
                         shifts_[kShiftResidual],
                         half ? kHalfStep : kChannelStep};
        built_ = true;
        return true;
    }

    std::vector<int> shifts_;
    int frames_ = 0;
    int width_ = 0;
    int channels_ = 0;
    int padded_ = 0;
    int padded_row_ = 0;
    int8_t *rows_ = nullptr;
    int8_t *x_ring_ = nullptr;
    int8_t *c_ring_ = nullptr;
    int8_t *a_ = nullptr;
    int8_t *d_ = nullptr;
    std::array<int32_t, 3> dw1_run_ = {};
    std::array<int32_t, 3> dw2_run_ = {};
    std::array<int32_t, 3> pad_run_ = {};
    std::array<int32_t, kNormSize> norm_run_ = {};
    std::array<int32_t, kResidualSize> residual_run_ = {};
    bool built_ = false;
};

} // namespace

dl::module::Module *conv_block_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return ConvBlock::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
