#include "core/espdl/espdl_group_norm_s8.hpp"

#include <algorithm>
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

namespace ai {

namespace {

const char *TAG = "group_norm_s8";

constexpr size_t kRank = 3;
constexpr size_t kMapRank = 4;
constexpr int kInterleaved = 4; // groups whose FMA chains overlap
constexpr int kTableSize = 256;
constexpr int kTableOffset = 128;

int8_t saturated(int v) noexcept
{
    return static_cast<int8_t>(std::max(std::min(v, static_cast<int>(INT8_MAX)), static_cast<int>(INT8_MIN)));
}

// x [1, T, C] or [1, T, F, C] read as [1, T, F * C], groups of C / G consecutive channels; the Mul's weights
// and the Add's bias [C], the bias on y's exponent; y [1, T, C] (esp_ppq_patches.group_norm_s8_from).
class GroupNormS8 final : public dl::module::Module {
  public:
    GroupNormS8(const char *name, int groups, float epsilon, float gamma, float beta, int norm_exponent,
                int mul_shift)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), groups_(groups), epsilon_(epsilon),
          gamma_(gamma), beta_(beta), norm_exponent_(norm_exponent), mul_shift_(mul_shift)
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
        if (x.size() == kMapRank) { return {{x[0], x[1], x[2] * x[3]}}; }
        return {x};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[0]);
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        dl::TensorBase *w = context->get_tensor(m_inputs_index[1]);
        dl::TensorBase *b = context->get_tensor(m_inputs_index[2]);
        if (!built_ && !build(x, y, w, b)) { return; }
        const int8_t *xs = x->get_element_ptr<int8_t>();
        means(xs);
        for (int g = 0; g < groups_; g += kInterleaved) {
            tables(xs, g, std::min(kInterleaved, groups_ - g));
        }
        outputs(xs, y->get_element_ptr<int8_t>(), w->get_element_ptr<int8_t>(), b->get_element_ptr<int8_t>());
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
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || groups < 1 || mul_shift < 1) {
            ESP_LOGE(TAG,
                     "%s: only an int8 GroupNorm chain of 1 group or more, mul shift 1 or more, runs here",
                     node_name.c_str());
            return nullptr;
        }
        return new GroupNormS8(node_name.c_str(), groups, epsilon, gamma, beta, norm_exponent, mul_shift);
    }

  private:
    bool build(dl::TensorBase *x, dl::TensorBase *y, dl::TensorBase *w, dl::TensorBase *b)
    {
        const size_t rank = x->shape.size();
        const int frames = rank == kRank || rank == kMapRank ? x->shape[1] : 0;
        const int channels = frames > 0 ? x->get_size() / frames : 0;
        if (frames == 0 || x->shape[0] != 1 || y->shape != std::vector<int>{1, frames, channels} ||
            x->get_dtype() != dl::DATA_TYPE_INT8 || y->get_dtype() != dl::DATA_TYPE_INT8 ||
            channels % groups_ != 0 || w->get_size() != channels || b->get_size() != channels) {
            ESP_LOGE(TAG, "%s: not an int8 [1, T, C] map of %d groups with C weights and biases", name,
                     groups_);
            return false;
        }
        frames_ = frames;
        channels_ = channels;
        width_ = channels_ / groups_;
        n_ = width_ * frames_;
        in_scale_ = DL_SCALE(x->get_exponent());
        per_value_ = 1.0f / static_cast<float>(n_) * (in_scale_ * in_scale_);
        norm_rescale_ = DL_RESCALE(norm_exponent_);
        tables_ =
            static_cast<int8_t *>(heap_caps_calloc(groups_, kTableSize + sizeof(float), MALLOC_CAP_SPIRAM));
        if (tables_ == nullptr) {
            ESP_LOGE(TAG, "%s: no memory for %d groups' tables", name, groups_);
            return false;
        }
        means_ = reinterpret_cast<float *>(tables_ + groups_ * kTableSize);
        built_ = true;
        return true;
    }

    // Each group's mean as esp-dl's double gives it from the exact int8 sum, the map read row after row.
    void means(const int8_t *xs) const noexcept
    {
        for (int g = 0; g < groups_; g++) {
            int sum = 0;
            for (int t = 0; t < frames_; t++) {
                const int8_t *at = xs + t * channels_ + g * width_;
                for (int j = 0; j < width_; j++) {
                    sum += at[j];
                }
            }
            means_[g] = static_cast<float>(sum * (1.0 / n_));
        }
    }

    // count groups' squares in the LayerNorm's order, channel after channel then frame after frame, their FMA
    // chains side by side; then each group's int8 norm of every int8 value, the float steps once per value.
    void tables(const int8_t *xs, int g0, int count) const noexcept
    {
        float squares[kInterleaved] = {};
        for (int j = 0; j < width_; j++) {
            for (int t = 0; t < frames_; t++) {
                const int8_t *at = xs + t * channels_ + g0 * width_ + j;
                for (int k = 0; k < count; k++) {
                    const float d = static_cast<float>(at[k * width_]) - means_[g0 + k];
                    squares[k] = fmaf(d, d, squares[k]);
                }
            }
        }
        for (int k = 0; k < count; k++) {
            const float mean = means_[g0 + k];
            const float scale = divide(1.0f, sqrt_newton(fmaf(per_value_, squares[k], epsilon_))) * in_scale_;
            const float gs = gamma_ * scale;
            int8_t *table = tables_ + (g0 + k) * kTableSize;
            for (int v = 0; v < kTableSize; v++) {
                table[v] = round_half_up((gs * (static_cast<float>(v - kTableOffset) - mean) + beta_) *
                                         norm_rescale_);
            }
        }
    }

    // Each value's int8 norm from its group's table, its Mul rounded half up and its bias added, both
    // saturating.
    void outputs(const int8_t *xs, int8_t *ys, const int8_t *w, const int8_t *b) const noexcept
    {
        for (int t = 0; t < frames_; t++) {
            const int8_t *row = xs + t * channels_;
            int8_t *out = ys + t * channels_;
            for (int g = 0; g < groups_; g++) {
                const int8_t *table = tables_ + g * kTableSize + kTableOffset;
                for (int c = g * width_; c < (g + 1) * width_; c++) {
                    const int m = saturated(((table[row[c]] * w[c] >> (mul_shift_ - 1)) + 1) >> 1);
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
    int frames_ = 0;
    int channels_ = 0;
    int width_ = 0;
    int n_ = 0;
    float in_scale_ = 0.0f;
    float per_value_ = 0.0f;
    float norm_rescale_ = 0.0f;
    int8_t *tables_ = nullptr; // per group, each int8 value's int8 norm; then means
    float *means_ = nullptr;
    bool built_ = false;
};

} // namespace

dl::module::Module *group_norm_s8_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return GroupNormS8::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
