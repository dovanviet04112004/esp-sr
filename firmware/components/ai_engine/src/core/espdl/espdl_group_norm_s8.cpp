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
#include "esp_log.h"
#include "fbs_model.hpp"

namespace ai {

namespace {

const char *TAG = "group_norm_s8";

constexpr int kRank = 3;

int8_t saturated(int v) noexcept
{
    return static_cast<int8_t>(std::max(std::min(v, static_cast<int>(INT8_MAX)), static_cast<int>(INT8_MIN)));
}

// Inputs as esp_ppq_patches.group_norm_s8_from leaves them: x [1, T, C] whose groups are C / G consecutive
// channels of every frame, the Mul's weights [C], the Add's bias [C] on the output's exponent.
class GroupNormS8 final : public dl::module::Module {
  public:
    GroupNormS8(const char *name, int groups, float epsilon, float gamma, float beta, int norm_exponent,
                int mul_shift)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), groups_(groups), epsilon_(epsilon),
          gamma_(gamma), beta_(beta), norm_exponent_(norm_exponent), mul_shift_(mul_shift)
    {}

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        return {input_shapes[0]};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[0]);
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        dl::TensorBase *w = context->get_tensor(m_inputs_index[1]);
        dl::TensorBase *b = context->get_tensor(m_inputs_index[2]);
        if (!built_ && !build(x, y, w, b)) { return; }
        for (int g = 0; g < groups_; g++) {
            run_group(x->get_element_ptr<int8_t>(), y->get_element_ptr<int8_t>(),
                      w->get_element_ptr<int8_t>(), b->get_element_ptr<int8_t>(), g * width_);
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
        if (x->shape.size() != kRank || x->shape[0] != 1 || x->shape != y->shape ||
            x->get_dtype() != dl::DATA_TYPE_INT8 || y->get_dtype() != dl::DATA_TYPE_INT8 ||
            x->shape[2] % groups_ != 0 || w->get_size() != x->shape[2] || b->get_size() != x->shape[2]) {
            ESP_LOGE(TAG, "%s: not an int8 [1, T, C] map of %d groups with C weights and biases", name,
                     groups_);
            return false;
        }
        frames_ = x->shape[1];
        channels_ = x->shape[2];
        width_ = channels_ / groups_;
        n_ = width_ * frames_;
        in_scale_ = DL_SCALE(x->get_exponent());
        per_value_ = 1.0f / static_cast<float>(n_) * (in_scale_ * in_scale_);
        norm_rescale_ = DL_RESCALE(norm_exponent_);
        built_ = true;
        return true;
    }

    // The LayerNorm of one group, its squares in the LayerNorm's own order, channel after channel then frame
    // after frame; then each value's int8 norm, its Mul rounded half up and its bias added, both saturating.
    void run_group(const int8_t *xs, int8_t *ys, const int8_t *w, const int8_t *b, int c0) const noexcept
    {
        int sum = 0;
        for (int t = 0; t < frames_; t++) {
            for (int j = 0; j < width_; j++) {
                sum += xs[t * channels_ + c0 + j];
            }
        }
        const float mean = static_cast<float>(sum * (1.0 / n_));
        float squares = 0.0f;
        for (int j = 0; j < width_; j++) {
            for (int t = 0; t < frames_; t++) {
                const float d = static_cast<float>(xs[t * channels_ + c0 + j]) - mean;
                squares = fmaf(d, d, squares);
            }
        }
        const float scale = divide(1.0f, sqrt_newton(fmaf(per_value_, squares, epsilon_))) * in_scale_;
        const float gs = gamma_ * scale;
        for (int t = 0; t < frames_; t++) {
            for (int j = 0; j < width_; j++) {
                const int c = c0 + j;
                const float d = static_cast<float>(xs[t * channels_ + c]) - mean;
                const int normed = round_half_up((gs * d + beta_) * norm_rescale_);
                const int product = normed * w[c];
                const int m = saturated(((product >> (mul_shift_ - 1)) + 1) >> 1);
                ys[t * channels_ + c] = saturated(m + b[c]);
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
    bool built_ = false;
};

} // namespace

dl::module::Module *group_norm_s8_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return GroupNormS8::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
