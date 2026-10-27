#include "core/espdl/espdl_matmul_s8.hpp"

#include <array>
#include <cstdint>
#include <string>
#include <utility>
#include <vector>

#include "dl_module_base.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "fbs_model.hpp"

extern "C" {
void ai_engine_pad_row_s8_esp32s3(int8_t *out, const int8_t *in, const int32_t *run);
void ai_engine_pw_bias_row_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                      const int32_t *run);
}

namespace ai {

namespace {

const char *TAG = "matmul_s8";

constexpr int kChannelStep = 16;
constexpr int kHalfStep = 8;
constexpr size_t kVectorAlignBytes = 16;

enum Input { kRows = 0, kWeights, kBias };
// Slots of ai_engine_pw_bias_row_s8_esp32s3's run.
enum Run { kRunBias = 6, kRunSize = 9 };

int padded(int channels) noexcept
{
    return (channels + kChannelStep - 1) / kChannelStep * kChannelStep;
}

// Inputs as esp_ppq_patches.matmul_s8_from lays them: rows [..., K], weights [Np / 16][K][16] and the bias
// [Np] if any, Np being N padded with zeros to a multiple of 16; "columns" is N.
class MatMulS8 final : public dl::module::Module {
  public:
    MatMulS8(const char *name, int shift, int relu, int outputs)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), shift_(shift), relu_(relu),
          outputs_(outputs)
    {}

    ~MatMulS8() override
    {
        heap_caps_free(rows_);
    }

    MatMulS8(const MatMulS8 &) = delete;
    MatMulS8 &operator=(const MatMulS8 &) = delete;

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        std::vector<int> shape = input_shapes[kRows];
        if (shape.empty()) { return {{outputs_}}; }
        shape[shape.size() - 1] = outputs_;
        return {shape};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[kRows]);
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        if (!built_ && !build(context, x, y)) { return; }
        const int8_t *in = x->get_element_ptr<int8_t>();
        if (rows_ != nullptr) {
            ai_engine_pad_row_s8_esp32s3(rows_, in, pad_run_.data());
            in = rows_;
        }
        const int8_t *weights = context->get_tensor(m_inputs_index[kWeights])->get_element_ptr<int8_t>();
        ai_engine_pw_bias_row_s8_esp32s3(y->get_element_ptr<int8_t>(), in, weights, run_.data());
    }

    void print() override
    {
        ESP_LOGI(TAG, "shift: %d, relu: %d, outputs: %d, quant_type: %s.", shift_, relu_, outputs_,
                 dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        int shift = 0;
        int relu = 0;
        int outputs = 0;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "shift", shift);
        fbs_model->get_operation_attribute(node_name, "relu", relu);
        fbs_model->get_operation_attribute(node_name, "columns", outputs);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || shift < 1 || outputs % kHalfStep != 0 || outputs <= 0) {
            ESP_LOGE(
                TAG,
                "%s: only an int8 product of shift 1 or more into a multiple of 8 outputs runs as MatMulS8",
                node_name.c_str());
            return nullptr;
        }
        return new MatMulS8(node_name.c_str(), shift, relu, outputs);
    }

  private:
    static bool aligned(const void *p) noexcept
    {
        return reinterpret_cast<uintptr_t>(p) % kVectorAlignBytes == 0;
    }

    bool build(dl::ModelContext *context, dl::TensorBase *x, dl::TensorBase *y)
    {
        const int k = x->shape.empty() ? 0 : x->shape.back();
        const int np = padded(outputs_);
        dl::TensorBase *weights = context->get_tensor(m_inputs_index[kWeights]);
        dl::TensorBase *bias =
            m_inputs_index.size() > kBias ? context->get_tensor(m_inputs_index[kBias]) : nullptr;
        if (k <= 0 || k % kHalfStep != 0 || x->get_dtype() != dl::DATA_TYPE_INT8 ||
            y->get_dtype() != dl::DATA_TYPE_INT8 || weights->get_size() != np * k ||
            !aligned(weights->get_element_ptr()) ||
            (bias != nullptr && (bias->get_size() != np || !aligned(bias->get_element_ptr())))) {
            ESP_LOGE(TAG, "%s: rows of %d, or weights and bias not laid for %d outputs", name, k, outputs_);
            return false;
        }
        const int rows = x->get_size() / k;
        const int kp = padded(k);
        const int whole = k / kChannelStep;
        const int half = k % kChannelStep != 0;
        if (half || !aligned(x->get_element_ptr())) {
            rows_ = static_cast<int8_t *>(
                heap_caps_aligned_calloc(kVectorAlignBytes, rows, kp, MALLOC_CAP_SPIRAM));
            if (rows_ == nullptr) {
                ESP_LOGE(TAG, "%s: no memory for %d padded rows", name, rows);
                return false;
            }
        }
        pad_run_ = {rows, whole, half};
        // Whole input vectors whose last product loads the next; the tail then sums the last 16, or the 8
        // left.
        const int ahead = half ? whole : whole - 1;
        const int last = outputs_ % kChannelStep != 0 ? kHalfStep : kChannelStep;
        run_ = {rows, np / kChannelStep, kp / kChannelStep, ahead, half, shift_, 0, relu_, last};
        run_[kRunBias] = bias == nullptr ? 0 : reinterpret_cast<int32_t>(bias->get_element_ptr());
        built_ = true;
        return true;
    }

    int shift_;
    int relu_;
    int outputs_;
    int8_t *rows_ = nullptr; // input rows in 16-byte vectors, if not already
    std::array<int32_t, 3> pad_run_ = {};
    std::array<int32_t, kRunSize> run_ = {};
    bool built_ = false;
};

} // namespace

dl::module::Module *matmul_s8_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return MatMulS8::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
