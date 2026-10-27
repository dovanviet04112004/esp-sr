#include "core/espdl/espdl_group_norm_s8.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "core/espdl/espdl_float_s3.hpp"
#include "dl_define.hpp"
#include "dl_module_base.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "fbs_model.hpp"

extern "C" {
void ai_engine_gn_sums_s8_esp32s3(int32_t *sums, const int8_t *x, const int32_t *run);
void ai_engine_gn_squares_s8_esp32s3(float *squares, const int8_t *x, const float *deviations,
                                     const int32_t *run);
void ai_engine_gn_squares_x2_s8_esp32s3(float *squares, const int8_t *x, const float *deviations,
                                        const int32_t *run);
void ai_engine_gn_squares_x4_s8_esp32s3(float *squares, const int8_t *x, const float *deviations,
                                        const int32_t *run);
void ai_engine_gn_outputs_s8_esp32s3(int8_t *y, const int8_t *x, const int8_t *tables, const int32_t *run);
}

namespace ai {

namespace {

const char *TAG = "group_norm_s8";

constexpr size_t kRank = 3;
constexpr size_t kMapRank = 4;
constexpr int kBatch = 2; // groups whose rows stay cached through every pass
constexpr int kTableSize = 256;
constexpr int kBlock = 16;
constexpr int kHalfBlock = 8;
constexpr size_t kVectorAlignBytes = 16;

// Slots of group_norm_s8_esp32s3.S's runs.
enum SumsRun { kSumsFrames = 0, kSumsStride, kSumsWidth, kSumsGroups, kSumsSize };
enum SquaresRun { kSquaresFrames = 0, kSquaresStride, kSquaresWidth, kSquaresSize };
enum OutputsRun {
    kOutFrames = 0,
    kOutColumns,
    kOutWidth,
    kOutGroups,
    kOutShiftLess1,
    kOutWeights,
    kOutBias,
    kOutBuffer,
    kOutStride,
    kOutRepeat,
    kOutSize
};

using SquaresKernel = void (*)(float *squares, const int8_t *x, const float *deviations, const int32_t *run);

bool aligned(const void *p) noexcept
{
    return reinterpret_cast<uintptr_t>(p) % kVectorAlignBytes == 0;
}

int32_t word_of(const void *p) noexcept
{
    return static_cast<int32_t>(reinterpret_cast<uintptr_t>(p));
}

int8_t saturated(int v) noexcept
{
    return static_cast<int8_t>(std::max(std::min(v, static_cast<int>(INT8_MAX)), static_cast<int>(INT8_MIN)));
}

// x [1, T, C] or [1, T, F, C] read as [1, T, F * C], C / G channels a group, each frame "repeat" times; Mul's
// weights and Add's bias [C] on y's exponent; y [1, T * repeat, C] (esp_ppq_patches.group_norm_s8_from).
class GroupNormS8 final : public dl::module::Module {
  public:
    GroupNormS8(const char *name, int groups, float epsilon, float gamma, float beta, int norm_exponent,
                int mul_shift, int repeat)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), groups_(groups), epsilon_(epsilon),
          gamma_(gamma), beta_(beta), norm_exponent_(norm_exponent), mul_shift_(mul_shift), repeat_(repeat)
    {}

    ~GroupNormS8() override
    {
        heap_caps_free(tables_);
    }

    GroupNormS8(const GroupNormS8 &) = delete;
    GroupNormS8 &operator=(const GroupNormS8 &) = delete;

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        const std::vector<int> &x = input_shapes[0];
        if (x.size() == kMapRank) { return {{x[0], x[1] * repeat_, x[2] * x[3]}}; }
        return {{x[0], x[1] * repeat_, x[2]}};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[0]);
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        dl::TensorBase *w = context->get_tensor(m_inputs_index[1]);
        dl::TensorBase *b = context->get_tensor(m_inputs_index[2]);
        if (!built_ && !build(x, y, w, b)) { return; }
        const int8_t *xs = x->get_element_ptr<int8_t>();
        int8_t *ys = y->get_element_ptr<int8_t>();
        const int8_t *ws = w->get_element_ptr<int8_t>();
        const int8_t *bs = b->get_element_ptr<int8_t>();
        for (int g = 0; g < groups_; g += kBatch) {
            const int count = std::min(kBatch, groups_ - g);
            float squares[kBatch] = {};
            means(xs, g, count);
            squares_of(xs, g, count, squares);
            tables(g, count, squares);
            outputs(xs, ys, ws, bs, g, count);
        }
    }

    void print() override
    {
        ESP_LOGI(TAG, "groups: %d, epsilon: %g, norm exponent: %d, mul shift: %d, quant_type: %s.", groups_,
                 static_cast<double>(epsilon_), norm_exponent_, mul_shift_,
                 dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        int groups = 0;
        int norm_exponent = 0;
        int mul_shift = 0;
        int repeat = 1;
        float epsilon = 1e-5f;
        float gamma = 1.0f;
        float beta = 0.0f;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "groups", groups);
        fbs_model->get_operation_attribute(node_name, "epsilon", epsilon);
        fbs_model->get_operation_attribute(node_name, "gamma", gamma);
        fbs_model->get_operation_attribute(node_name, "beta", beta);
        fbs_model->get_operation_attribute(node_name, "norm_exponent", norm_exponent);
        fbs_model->get_operation_attribute(node_name, "mul_shift", mul_shift);
        fbs_model->get_operation_attribute(node_name, "repeat", repeat);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || groups < 1 || mul_shift < 1 || repeat < 1) {
            ESP_LOGE(TAG,
                     "%s: only an int8 GroupNorm chain of 1 group or more, mul shift and repeat 1 or more, "
                     "runs here",
                     node_name.c_str());
            return nullptr;
        }
        return new GroupNormS8(node_name.c_str(), groups, epsilon, gamma, beta, norm_exponent, mul_shift,
                               repeat);
    }

  private:
    bool build(dl::TensorBase *x, dl::TensorBase *y, dl::TensorBase *w, dl::TensorBase *b)
    {
        const size_t rank = x->shape.size();
        const int frames = rank == kRank || rank == kMapRank ? x->shape[1] : 0;
        const int channels = frames > 0 ? x->get_size() / frames : 0;
        if (frames == 0 || x->shape[0] != 1 || y->shape != std::vector<int>{1, frames * repeat_, channels} ||
            x->get_dtype() != dl::DATA_TYPE_INT8 || y->get_dtype() != dl::DATA_TYPE_INT8 ||
            channels % groups_ != 0 || w->get_size() != channels || b->get_size() != channels) {
            ESP_LOGE(TAG, "%s: not an int8 [1, T, C] map of %d groups with C weights and biases", name,
                     groups_);
            return false;
        }
        source_frames_ = frames;
        frames_ = frames * repeat_;
        channels_ = channels;
        width_ = channels_ / groups_;
        n_ = width_ * frames_;
        in_scale_ = DL_SCALE(x->get_exponent());
        per_value_ = 1.0f / static_cast<float>(n_) * (in_scale_ * in_scale_);
        norm_rescale_ = DL_RESCALE(norm_exponent_);
        const size_t batch_bytes = (kBatch * width_ + kBlock - 1) / kBlock * kBlock;
        const size_t bytes =
            groups_ * kTableSize + batch_bytes + (kBatch * kTableSize + groups_) * sizeof(float);
        tables_ =
            static_cast<int8_t *>(heap_caps_aligned_calloc(kVectorAlignBytes, 1, bytes, MALLOC_CAP_SPIRAM));
        if (tables_ == nullptr) {
            ESP_LOGE(TAG, "%s: no memory for %d groups' tables", name, groups_);
            return false;
        }
        int8_t *buffer = tables_ + groups_ * kTableSize;
        deviations_ = reinterpret_cast<float *>(buffer + batch_bytes);
        means_ = deviations_ + kBatch * kTableSize;
        vector_ = groups_ % kBatch == 0 && width_ % kHalfBlock == 0 && channels_ % kBlock == 0 &&
                  aligned(x->get_element_ptr()) && aligned(y->get_element_ptr()) &&
                  aligned(w->get_element_ptr()) && aligned(b->get_element_ptr());
        sums_run_ = {source_frames_, channels_, width_, kBatch};
        squares_run_ = {source_frames_, channels_, width_};
        outputs_run_ = {source_frames_,  kBatch * width_, width_, kBatch, mul_shift_ - 1, 0, 0,
                        word_of(buffer), channels_,       repeat_};
        squares_kernel_ = repeat_ == 1   ? ai_engine_gn_squares_s8_esp32s3
                          : repeat_ == 2 ? ai_engine_gn_squares_x2_s8_esp32s3
                          : repeat_ == 4 ? ai_engine_gn_squares_x4_s8_esp32s3
                                         : nullptr;
        built_ = true;
        return true;
    }

    // Each group's mean as esp-dl's double gives it from the exact int8 sum.
    void means(const int8_t *xs, int g0, int count) const noexcept
    {
        int32_t sums[kBatch] = {};
        if (vector_) {
            ai_engine_gn_sums_s8_esp32s3(sums, xs + g0 * width_, sums_run_.data());
        } else {
            for (int k = 0; k < count; k++) {
                for (int t = 0; t < source_frames_; t++) {
                    const int8_t *at = xs + t * channels_ + (g0 + k) * width_;
                    for (int j = 0; j < width_; j++) {
                        sums[k] += at[j];
                    }
                }
            }
        }
        for (int k = 0; k < count; k++) {
            means_[g0 + k] = static_cast<float>(sums[k] * repeat_ * (1.0 / n_));
        }
    }

    // The batch's squares in the LayerNorm's order, channel after channel then frame after frame.
    void squares_of(const int8_t *xs, int g0, int count, float *squares) const noexcept
    {
        if (vector_ && squares_kernel_ != nullptr) {
            for (int k = 0; k < kBatch; k++) {
                float *deviation = deviations_ + k * kTableSize;
                for (int v = INT8_MIN; v <= INT8_MAX; v++) {
                    deviation[static_cast<uint8_t>(v)] = static_cast<float>(v) - means_[g0 + k];
                }
            }
            squares_kernel_(squares, xs + g0 * width_, deviations_, squares_run_.data());
            return;
        }
        for (int j = 0; j < width_; j++) {
            for (int t = 0; t < frames_; t++) {
                const int8_t *at = xs + t / repeat_ * channels_ + g0 * width_ + j;
                for (int k = 0; k < count; k++) {
                    const float d = static_cast<float>(at[k * width_]) - means_[g0 + k];
                    squares[k] = fmaf(d, d, squares[k]);
                }
            }
        }
    }

    // Each group's int8 norm of every int8 value, the float steps once per value.
    void tables(int g0, int count, const float *squares) const noexcept
    {
        for (int k = 0; k < count; k++) {
            const float mean = means_[g0 + k];
            const float scale = divide(1.0f, sqrt_newton(fmaf(per_value_, squares[k], epsilon_))) * in_scale_;
            const float gs = gamma_ * scale;
            int8_t *table = tables_ + (g0 + k) * kTableSize;
            for (int v = INT8_MIN; v <= INT8_MAX; v++) {
                table[static_cast<uint8_t>(v)] =
                    round_half_up((gs * (static_cast<float>(v) - mean) + beta_) * norm_rescale_);
            }
        }
    }

    // The batch's channels of every frame: each value's int8 norm from its group's table, its Mul rounded
    // half up and its bias added, both saturating.
    void outputs(const int8_t *xs, int8_t *ys, const int8_t *w, const int8_t *b, int g0, int count) noexcept
    {
        const int c0 = g0 * width_;
        if (vector_) {
            outputs_run_[kOutWeights] = word_of(w + c0);
            outputs_run_[kOutBias] = word_of(b + c0);
            ai_engine_gn_outputs_s8_esp32s3(ys + c0, xs + c0, tables_ + g0 * kTableSize, outputs_run_.data());
            return;
        }
        for (int t = 0; t < frames_; t++) {
            const int8_t *row = xs + t / repeat_ * channels_;
            int8_t *out = ys + t * channels_;
            for (int g = g0; g < g0 + count; g++) {
                const int8_t *table = tables_ + g * kTableSize;
                for (int c = g * width_; c < (g + 1) * width_; c++) {
                    const int m = saturated(
                        ((table[static_cast<uint8_t>(row[c])] * w[c] >> (mul_shift_ - 1)) + 1) >> 1);
                    out[c] = saturated(m + b[c]);
                }
            }
        }
    }

    int groups_;
    float epsilon_;
    float gamma_;
    float beta_;
    int norm_exponent_;
    int mul_shift_;
    int repeat_;
    int source_frames_ = 0;
    int frames_ = 0; // repeat times x's
    int channels_ = 0;
    int width_ = 0;
    int n_ = 0;
    float in_scale_ = 0.0f;
    float per_value_ = 0.0f;
    float norm_rescale_ = 0.0f;
    int8_t *tables_ = nullptr; // per group, by the value's byte, its int8 norm
    float *deviations_ = nullptr;
    float *means_ = nullptr;
    std::array<int32_t, kSumsSize> sums_run_ = {};
    std::array<int32_t, kSquaresSize> squares_run_ = {};
    std::array<int32_t, kOutSize> outputs_run_ = {};
    SquaresKernel squares_kernel_ = nullptr; // nullptr: no hand-written one for this repeat
    bool vector_ = false;
    bool built_ = false;
};

} // namespace

dl::module::Module *group_norm_s8_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return GroupNormS8::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
