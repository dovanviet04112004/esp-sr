#include "core/espdl/espdl_layernorm_s8.hpp"

#include <climits>
#include <cmath>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "dl_define.hpp"
#include "dl_module_base.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "fbs_model.hpp"

namespace ai {

namespace {

const char *TAG = "layernorm_s8";

constexpr float kNewtonStop = 0.00001f; // EN of esp-dl's dl_math.hpp

// esp-dl's dl::math::sqrt_newton: halving is exact, so its double 0.5 gives what float does.
float sqrt_newton(float x) noexcept
{
    if (x == 0.0f) { return 0.0f; }
    float result = x;
    float last = 0.0f;
    do {
        last = result;
        result = (last + x / last) * 0.5f;
    } while (fabsf(result - last) > kNewtonStop);
    return result;
}

// esp-dl's floor(v + 0.5) in double then its int8 clip, from float steps that are exact below 2^23.
int8_t round_half_up(float v) noexcept
{
    if (v >= static_cast<float>(INT8_MAX)) { return INT8_MAX; }
    if (v <= static_cast<float>(INT8_MIN)) { return INT8_MIN; }
    int whole = static_cast<int>(v);
    if (static_cast<float>(whole) > v) { whole -= 1; }
    return static_cast<int8_t>(whole + (v - static_cast<float>(whole) >= 0.5f ? 1 : 0));
}

// A count whose odd part is 1 or 3 divides an int8 sum in float exactly as esp-dl's double does
// (test_esp_ppq_patches checks every sum).
bool float_mean_matches(int n) noexcept
{
    while (n % 2 == 0) {
        n /= 2;
    }
    return n == 1 || n == 3;
}

// Inputs as esp_ppq_patches.layernorm_s8_from leaves them: x, gamma, beta if any, all int8 on one exponent
// each. In place as esp-dl's own: each value is read ahead of the write over it.
class LayerNormS8 final : public dl::module::Module {
  public:
    LayerNormS8(const char *name, float epsilon)
        : Module(name, dl::MODULE_INPLACE_CHANGED_BUFFER, dl::QUANT_TYPE_SYMM_8BIT), epsilon_(epsilon)
    {}

    ~LayerNormS8() override
    {
        heap_caps_free(gamma_);
    }

    LayerNormS8(const LayerNormS8 &) = delete;
    LayerNormS8 &operator=(const LayerNormS8 &) = delete;

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        return {input_shapes[0]};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[0]);
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        if (!built_ && !build(context, x, y)) { return; }
        const int8_t *xs = x->get_element_ptr<int8_t>();
        int8_t *ys = y->get_element_ptr<int8_t>();
        for (int p = 0; p < positions_; p++) {
            run_position(xs + p * n_, ys + p * n_);
        }
    }

    void print() override
    {
        ESP_LOGI(TAG, "epsilon: %g, quant_type: %s.", static_cast<double>(epsilon_),
                 dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        int axis = -1;
        float epsilon = 1e-5f;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "axis", axis);
        fbs_model->get_operation_attribute(node_name, "epsilon", epsilon);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || axis != -1) {
            ESP_LOGE(TAG, "%s: only an int8 norm over the last axis runs as LayerNormS8", node_name.c_str());
            return nullptr;
        }
        return new LayerNormS8(node_name.c_str(), epsilon);
    }

  private:
    bool build(dl::ModelContext *context, dl::TensorBase *x, dl::TensorBase *y)
    {
        dl::TensorBase *gamma = m_inputs_index.size() > 1 ? context->get_tensor(m_inputs_index[1]) : nullptr;
        dl::TensorBase *beta = m_inputs_index.size() > 2 ? context->get_tensor(m_inputs_index[2]) : nullptr;
        n_ = x->shape.empty() ? 0 : x->shape.back();
        if (n_ <= 0 || gamma == nullptr || gamma->get_size() != n_ ||
            (beta != nullptr && beta->get_size() != n_) || x->get_dtype() != dl::DATA_TYPE_INT8 ||
            y->get_dtype() != dl::DATA_TYPE_INT8 || x->shape != y->shape) {
            ESP_LOGE(TAG, "%s: not an int8 norm of %d values with gamma", name, n_);
            return false;
        }
        gamma_ = static_cast<float *>(heap_caps_malloc(2 * n_ * sizeof(float), MALLOC_CAP_SPIRAM));
        if (gamma_ == nullptr) {
            ESP_LOGE(TAG, "%s: no memory for %d gammas and betas", name, n_);
            return false;
        }
        beta_ = gamma_ + n_;
        const int8_t *g = gamma->get_element_ptr<int8_t>();
        const float g_scale = DL_SCALE(gamma->get_exponent());
        for (int j = 0; j < n_; j++) {
            gamma_[j] = g[j] * g_scale;
        }
        if (beta != nullptr) {
            const int8_t *b = beta->get_element_ptr<int8_t>();
            const float b_scale = DL_SCALE(beta->get_exponent());
            for (int j = 0; j < n_; j++) {
                beta_[j] = b[j] * b_scale;
            }
        }
        biased_ = beta != nullptr;
        positions_ = x->get_size() / n_;
        in_scale_ = DL_SCALE(x->get_exponent());
        per_value_ = 1.0f / static_cast<float>(n_) * (in_scale_ * in_scale_);
        out_rescale_ = DL_RESCALE(y->get_exponent());
        float_mean_ = float_mean_matches(n_);
        built_ = true;
        return true;
    }

    // layernorm_as_espdl's steps for one position, each a float32 operation rounded once.
    void run_position(const int8_t *x, int8_t *y) const noexcept
    {
        int sum = 0;
        for (int j = 0; j < n_; j++) {
            sum += x[j];
        }
        const float mean = float_mean_ ? static_cast<float>(sum) / static_cast<float>(n_)
                                       : static_cast<float>(sum * (1.0 / n_));
        float squares = 0.0f;
        for (int j = 0; j < n_; j++) {
            const float d = static_cast<float>(x[j]) - mean;
            squares = fmaf(d, d, squares);
        }
        const float scale = 1.0f / sqrt_newton(fmaf(per_value_, squares, epsilon_)) * in_scale_;
        for (int j = 0; j < n_; j++) {
            float result = gamma_[j] * scale * (static_cast<float>(x[j]) - mean);
            if (biased_) { result = result + beta_[j]; }
            y[j] = round_half_up(result * out_rescale_);
        }
    }

    float epsilon_;
    int n_ = 0;
    int positions_ = 0;
    float in_scale_ = 0.0f;
    float per_value_ = 0.0f;
    float out_rescale_ = 0.0f;
    float *gamma_ = nullptr; // gamma then beta, as float, one allocation
    float *beta_ = nullptr;
    bool biased_ = false;
    bool float_mean_ = false;
    bool built_ = false;
};

} // namespace

dl::module::Module *layernorm_s8_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return LayerNormS8::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
