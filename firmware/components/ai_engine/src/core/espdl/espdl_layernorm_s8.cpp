#include "core/espdl/espdl_layernorm_s8.hpp"

#include "core/espdl/espdl_float_s3.hpp"

#include <algorithm>
#include <array>
#include <climits>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "dl_define.hpp"
#include "dl_module_base.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "fbs_model.hpp"

extern "C" {
void ai_engine_ln_deviations_s8_esp32s3(float *dev, float *variances, const int8_t *x, int pairs,
                                        const int32_t *run);
void ai_engine_ln_scales_s8_esp32s3(float *scales, const float *variances, const int32_t *run, int pairs);
void ai_engine_ln_outputs_s8_esp32s3(int8_t *y, const float *dev, const float *scales, const float *gb,
                                     int pairs, const int32_t *run);
}

namespace ai {

namespace {

const char *TAG = "layernorm_s8";

// The kernel's root is esp-dl's over this range (test_esp_ppq_patches checks every float in it).
constexpr float kRootLow = 0x1p-3f;
constexpr float kRootHigh = 0x1p12f;
constexpr float kRootWindow = 0x1p-7f; // flagged band under each half ulp of x / root
constexpr int kLanes = 4;
constexpr int kBlock = 16;
constexpr int kWordBytes = 4;
// The paired kernels sum n bytes, each offset by 128, in 16-bit halves of a word.
constexpr int kMaxPaired = 512;

// Slots of layernorm_s8_esp32s3.S's run.
enum Run {
    kRunN = 0,
    kRunWords,
    kRunOffset,
    kRunInverseN,
    kRunCount,
    kRunPerValue,
    kRunEpsilon,
    kRunInScale,
    kRunWindow,
    kRunHalf,
    kRunSize
};

// For these counts an int8 sum times fl(1 / n), corrected by one FMA, is esp-dl's double quotient
// (test_esp_ppq_patches checks every such count against every sum).
bool float_mean_matches(int n) noexcept
{
    return n % kWordBytes == 0 && n <= kMaxPaired;
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
        if (paired_) {
            run_pairs(xs, ys);
            return;
        }
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
        const int floats = 2 * n_ + kBlock * n_ + 2 * kBlock + 2 * n_;
        gamma_ = static_cast<float *>(heap_caps_calloc(floats, sizeof(float), MALLOC_CAP_SPIRAM));
        if (gamma_ == nullptr) {
            ESP_LOGE(TAG, "%s: no memory for %d floats of gammas, betas and a block's deviations", name,
                     floats);
            return false;
        }
        beta_ = gamma_ + n_;
        deviations_ = beta_ + n_;
        variances_ = deviations_ + kBlock * n_;
        scales_ = variances_ + kBlock;
        gb_ = scales_ + kBlock;
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
        positions_ = x->get_size() / n_;
        in_scale_ = DL_SCALE(x->get_exponent());
        per_value_ = 1.0f / static_cast<float>(n_) * (in_scale_ * in_scale_);
        out_rescale_ = DL_RESCALE(y->get_exponent());
        float_mean_ = float_mean_matches(n_);
        count_ = static_cast<float>(n_);
        inverse_n_ = 1.0f / count_;
        for (int j = 0; j < n_; j++) {
            gb_[2 * j] = gamma_[j] * out_rescale_;
            gb_[2 * j + 1] = beta_[j] * out_rescale_;
        }
        run_ = {n_,
                n_ / kWordBytes,
                -INT8_MIN * n_,
                bits_of(inverse_n_),
                bits_of(count_),
                bits_of(per_value_),
                bits_of(epsilon_),
                bits_of(in_scale_),
                bits_of(kRootWindow),
                bits_of(0.5f)};
        paired_ = float_mean_ && n_ % kWordBytes == 0 && n_ <= kMaxPaired;
        built_ = true;
        return true;
    }

    // Blocks of up to kBlock positions, two a pass; in place, a block's y is written once all its x is read.
    void run_pairs(const int8_t *xs, int8_t *ys) const noexcept
    {
        int p = 0;
        while (positions_ - p >= 2) {
            const int count = std::min(kBlock, (positions_ - p) & ~1);
            const int pairs = count / 2;
            ai_engine_ln_deviations_s8_esp32s3(deviations_, variances_, xs + p * n_, pairs, run_.data());
            ai_engine_ln_scales_s8_esp32s3(scales_, variances_, run_.data(), pairs);
            for (int i = 0; i < count; i++) {
                const float v = variances_[i];
                if (scales_[i] == 0.0f || v < kRootLow || v >= kRootHigh) {
                    scales_[i] = divide(1.0f, sqrt_newton(v)) * in_scale_;
                }
            }
            ai_engine_ln_outputs_s8_esp32s3(ys + p * n_, deviations_, scales_, gb_, pairs, run_.data());
            p += count;
        }
        if (p < positions_) { run_position(xs + p * n_, ys + p * n_); }
    }

    // layernorm_as_espdl's steps for one position, each a float32 operation rounded once.
    void run_position(const int8_t *x, int8_t *y) const noexcept
    {
        int sum = 0;
        for (int j = 0; j < n_; j++) {
            sum += x[j];
        }
        float mean;
        if (float_mean_) {
            const float s = static_cast<float>(sum);
            const float q = s * inverse_n_;
            mean = fmaf(fmaf(-q, count_, s), inverse_n_, q);
        } else {
            mean = static_cast<float>(sum * (1.0 / n_));
        }
        float *d = deviations_;
        float squares = 0.0f;
        for (int j = 0; j < n_; j++) {
            d[j] = static_cast<float>(x[j]) - mean;
            squares = fmaf(d[j], d[j], squares);
        }
        const float scale = divide(1.0f, sqrt_newton(fmaf(per_value_, squares, epsilon_))) * in_scale_;
        // Four values at once so their float steps overlap; a zero beta adds nothing the rounding sees.
        int j = 0;
        for (; j + kLanes <= n_; j += kLanes) {
            const float r0 = gamma_[j] * scale * d[j] + beta_[j];
            const float r1 = gamma_[j + 1] * scale * d[j + 1] + beta_[j + 1];
            const float r2 = gamma_[j + 2] * scale * d[j + 2] + beta_[j + 2];
            const float r3 = gamma_[j + 3] * scale * d[j + 3] + beta_[j + 3];
            y[j] = round_half_up(r0 * out_rescale_);
            y[j + 1] = round_half_up(r1 * out_rescale_);
            y[j + 2] = round_half_up(r2 * out_rescale_);
            y[j + 3] = round_half_up(r3 * out_rescale_);
        }
        for (; j < n_; j++) {
            y[j] = round_half_up((gamma_[j] * scale * d[j] + beta_[j]) * out_rescale_);
        }
    }

    float epsilon_;
    int n_ = 0;
    int positions_ = 0;
    float in_scale_ = 0.0f;
    float per_value_ = 0.0f;
    float out_rescale_ = 0.0f;
    float count_ = 0.0f;
    float inverse_n_ = 0.0f;
    float *gamma_ = nullptr; // one allocation holds every buffer below
    float *beta_ = nullptr;
    float *deviations_ = nullptr;
    float *variances_ = nullptr;
    float *scales_ = nullptr;
    float *gb_ = nullptr; // gamma, beta interleaved, times out_rescale
    std::array<int32_t, kRunSize> run_ = {};
    bool float_mean_ = false;
    bool paired_ = false;
    bool built_ = false;
};

} // namespace

dl::module::Module *layernorm_s8_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return LayerNormS8::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
