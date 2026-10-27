#include "core/espdl/espdl_softmax_quant.hpp"

#include <algorithm>
#include <climits>
#include <cmath>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "dl_define.hpp"
#include "dl_module_base.hpp"
#include "dl_tool.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "fbs_model.hpp"

namespace ai {

namespace {

const char *TAG = "softmax_quant";

constexpr int kTableOffset = 128; // esp-dl's gen_lut_8bit: entry i is exp(s * (i - 128))
constexpr int kColumnsAtOnce = 4;

// esp-dl's floor(v + 0.5) in double, for the v >= 0 a probability gives, then its int8 clip: below 2^22 the
// float sum v + 0.5 rounds to no other integer part, so truncating it floors it.
int8_t round_half_up(float v) noexcept
{
    return static_cast<int8_t>(std::min(static_cast<int>(v + 0.5f), static_cast<int>(INT8_MAX)));
}

// Input esp-dl's int8 Softmax input, or after softmax_scale_from the map it scaled and the int8 products by
// byte on the Softmax's exponent; output the int8 tensor its QuantizeLinear wrote, on that node's exponent.
class SoftmaxQuant final : public dl::module::Module {
  public:
    SoftmaxQuant(const char *name, int axis)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), axis_(axis)
    {}

    ~SoftmaxQuant() override
    {
        heap_caps_free(table_);
        heap_caps_free(scales_);
    }

    SoftmaxQuant(const SoftmaxQuant &) = delete;
    SoftmaxQuant &operator=(const SoftmaxQuant &) = delete;

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
        const int block = len_ * inner_;
        for (int o = 0; o < outer_; o++) {
            run_block(xs + o * block, ys + o * block);
        }
    }

    void print() override
    {
        ESP_LOGI(TAG, "axis: %d, quant_type: %s.", axis_, dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        int axis = -1;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "axis", axis);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT) {
            ESP_LOGE(TAG, "%s: only an int8 Softmax runs joined to its quantiser", node_name.c_str());
            return nullptr;
        }
        return new SoftmaxQuant(node_name.c_str(), axis);
    }

  private:
    bool build(dl::ModelContext *context, dl::TensorBase *x, dl::TensorBase *y)
    {
        const int dims = static_cast<int>(x->shape.size());
        const int axis = axis_ < 0 ? axis_ + dims : axis_;
        if (axis < 0 || axis >= dims || x->get_dtype() != dl::DATA_TYPE_INT8 ||
            y->get_dtype() != dl::DATA_TYPE_INT8 || x->shape != y->shape) {
            ESP_LOGE(TAG, "%s: axis %d of %d, or the input and output are not one int8 shape", name, axis_,
                     dims);
            return false;
        }
        dl::TensorBase *products =
            m_inputs_index.size() > 1 ? context->get_tensor(m_inputs_index[1]) : nullptr;
        if (products != nullptr &&
            (products->get_size() != kSoftmaxTableSize || products->get_dtype() != dl::DATA_TYPE_INT8)) {
            ESP_LOGE(TAG, "%s: the scaled bytes are %d values, not one int8 each", name,
                     products->get_size());
            return false;
        }
        outer_ = 1;
        inner_ = 1;
        for (int d = 0; d < axis; d++) {
            outer_ *= x->shape[d];
        }
        for (int d = axis + 1; d < dims; d++) {
            inner_ *= x->shape[d];
        }
        len_ = x->shape[axis];
        table_ = static_cast<float *>(heap_caps_malloc(kSoftmaxTableSize * sizeof(float), MALLOC_CAP_SPIRAM));
        scales_ = static_cast<float *>(heap_caps_malloc(inner_ * sizeof(float), MALLOC_CAP_SPIRAM));
        if (table_ == nullptr || scales_ == nullptr) {
            ESP_LOGE(TAG, "%s: no memory for the table and %d column scales", name, inner_);
            return false;
        }
        const bool table_made = products == nullptr ? softmax_table_s8(table_, x->get_exponent(), nullptr)
                                                    : softmax_table_s8(table_, products->get_exponent(),
                                                                       products->get_element_ptr<int8_t>());
        if (!table_made) {
            ESP_LOGE(TAG, "%s: no memory for esp-dl's exp table", name);
            return false;
        }
        out_rescale_ = DL_RESCALE(y->get_exponent());
        built_ = true;
        return true;
    }

    void run_block(const int8_t *x, int8_t *y) const noexcept
    {
        softmax_block_s8(x, y, len_, inner_, table_, scales_, out_rescale_);
    }

    int axis_;
    int outer_ = 0;
    int len_ = 0;
    int inner_ = 0;
    float out_rescale_ = 0.0f;
    float *table_ = nullptr;  // esp-dl's table, entry by the int8 input's own byte
    float *scales_ = nullptr; // column sums, then their scales
    bool built_ = false;
};

} // namespace

bool softmax_table_s8(float *table, int exponent, const int8_t *products)
{
    float *esp_dl_table =
        static_cast<float *>(heap_caps_malloc(kSoftmaxTableSize * sizeof(float), MALLOC_CAP_SPIRAM));
    if (esp_dl_table == nullptr) { return false; }
    dl::tool::gen_lut_8bit(esp_dl_table, exponent, expf);
    for (int i = 0; i < kSoftmaxTableSize; i++) {
        const int byte = products == nullptr ? static_cast<uint8_t>(i - kTableOffset) : i;
        table[byte] = esp_dl_table[products == nullptr ? i : products[i] + kTableOffset];
    }
    heap_caps_free(esp_dl_table);
    return true;
}

void softmax_block_s8(const int8_t *x, int8_t *y, int len, int inner, const float *table, float *scales,
                      float out_rescale) noexcept
{
    const uint8_t *in = reinterpret_cast<const uint8_t *>(x);
    int k = 0;
    for (; k + kColumnsAtOnce <= inner; k += kColumnsAtOnce) {
        float s0 = 0.0f;
        float s1 = 0.0f;
        float s2 = 0.0f;
        float s3 = 0.0f;
        const uint8_t *p = in + k;
        for (int i = 0; i < len; i++, p += inner) {
            s0 += table[p[0]];
            s1 += table[p[1]];
            s2 += table[p[2]];
            s3 += table[p[3]];
        }
        scales[k] = s0;
        scales[k + 1] = s1;
        scales[k + 2] = s2;
        scales[k + 3] = s3;
    }
    for (; k < inner; k++) {
        float s = 0.0f;
        const uint8_t *p = in + k;
        for (int i = 0; i < len; i++, p += inner) {
            s += table[*p];
        }
        scales[k] = s;
    }
    // A power of two scales exactly, so one product rounds as esp-dl's p * (1 / sum) then p * 2^-e do.
    for (k = 0; k < inner; k++) {
        scales[k] = 1.0f / scales[k] * out_rescale;
    }
    for (int i = 0; i < len; i++) {
        const uint8_t *row = in + i * inner;
        int8_t *out = y + i * inner;
        for (k = 0; k + kColumnsAtOnce <= inner; k += kColumnsAtOnce) {
            const float v0 = table[row[k]] * scales[k];
            const float v1 = table[row[k + 1]] * scales[k + 1];
            const float v2 = table[row[k + 2]] * scales[k + 2];
            const float v3 = table[row[k + 3]] * scales[k + 3];
            out[k] = round_half_up(v0);
            out[k + 1] = round_half_up(v1);
            out[k + 2] = round_half_up(v2);
            out[k + 3] = round_half_up(v3);
        }
        for (; k < inner; k++) {
            out[k] = round_half_up(table[row[k]] * scales[k]);
        }
    }
}

dl::module::Module *softmax_quant_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return SoftmaxQuant::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
