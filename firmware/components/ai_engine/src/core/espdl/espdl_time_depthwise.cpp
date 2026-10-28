#include "core/espdl/espdl_time_depthwise.hpp"

#include <array>
#include <cstdint>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "core/espdl/espdl_loan.hpp"
#include "dl_module_base.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "fbs_model.hpp"

extern "C" {
void ai_engine_dw_time_s8_esp32s3(int8_t *out, const int8_t *x, const int8_t *taps, const int32_t *run);
}

namespace ai {

namespace {

const char *TAG = "time_depthwise";

constexpr size_t kRank = 3;
constexpr int kChannelStep = 16;
constexpr int kHalfStep = 8;
constexpr size_t kVectorAlignBytes = 16;

enum Input { kX = 0, kTaps, kBias };
// Slots of conv_block_s8_esp32s3.S's time depthwise run.
enum Run { kRunRows = 0, kRunVectors, kRunTaps, kRunShift, kRunBias, kRunSize };

// Padded rows, then the output rows, of whichever TimeDepthwise runs; it only grows, while graphs are built.
struct Scratch {
    int8_t *mem;
    size_t bytes;
};
Scratch s_scratch = {nullptr, 0};

bool scratch_holds(size_t bytes) noexcept
{
    if (bytes <= s_scratch.bytes) { return true; }
    heap_caps_free(s_scratch.mem);
    s_scratch.mem =
        static_cast<int8_t *>(heap_caps_aligned_alloc(kVectorAlignBytes, bytes, MALLOC_CAP_SPIRAM));
    s_scratch.bytes = s_scratch.mem != nullptr ? bytes : 0;
    return s_scratch.mem != nullptr;
}

int padded(int channels) noexcept
{
    return (channels + kChannelStep - 1) / kChannelStep * kChannelStep;
}

bool aligned(const void *p) noexcept
{
    return reinterpret_cast<uintptr_t>(p) % kVectorAlignBytes == 0;
}

int32_t word_of(const void *p) noexcept
{
    return static_cast<int32_t>(reinterpret_cast<uintptr_t>(p));
}

// Inputs as esp_ppq_patches.time_depthwise_from lays them: x [1, T, C], taps [K][Cp], bias [Cp]; Cp is C
// padded with zeros to a multiple of 16.
class TimeDepthwise final : public dl::module::Module {
  public:
    TimeDepthwise(const char *name, int shift)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), shift_(shift)
    {}

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        return {input_shapes[kX]};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[kX]);
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        if (!built_ && !build(context, x, y)) { return; }
        const int8_t *xs = x->get_element_ptr<int8_t>();
        int8_t *ys = y->get_element_ptr<int8_t>();
        const Loan lent = loan_lent();
        int8_t *rows = lent.bytes >= scratch_bytes_ ? lent.mem : s_scratch.mem;
        int8_t *out = rows + (frames_ + taps_ - 1) * padded_;
        const int edge = taps_ / 2 * padded_;
        memset(rows, 0, edge);
        memset(rows + edge + frames_ * padded_, 0, edge);
        for (int t = 0; t < frames_; t++) {
            memcpy(rows + edge + t * padded_, xs + t * channels_, channels_);
        }
        run_[kRunBias] = word_of(context->get_tensor(m_inputs_index[kBias])->get_element_ptr());
        ai_engine_dw_time_s8_esp32s3(
            out, rows, context->get_tensor(m_inputs_index[kTaps])->get_element_ptr<int8_t>(), run_.data());
        for (int t = 0; t < frames_; t++) {
            memcpy(ys + t * channels_, out + t * padded_, channels_);
        }
    }

    void print() override
    {
        ESP_LOGI(TAG, "shift: %d, quant_type: %s.", shift_, dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        int shift = 0;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "shift", shift);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || shift < 1) {
            ESP_LOGE(TAG, "%s: only an int8 depthwise convolution of shift 1 or more runs as TimeDepthwise",
                     node_name.c_str());
            return nullptr;
        }
        return new TimeDepthwise(node_name.c_str(), shift);
    }

  private:
    bool build(dl::ModelContext *context, dl::TensorBase *x, dl::TensorBase *y)
    {
        dl::TensorBase *taps = context->get_tensor(m_inputs_index[kTaps]);
        dl::TensorBase *bias = context->get_tensor(m_inputs_index[kBias]);
        if (x->shape.size() != kRank || x->shape[0] != 1 || x->shape != y->shape ||
            x->get_dtype() != dl::DATA_TYPE_INT8 || y->get_dtype() != dl::DATA_TYPE_INT8 ||
            x->shape[2] % kHalfStep != 0) {
            ESP_LOGE(TAG, "%s: x and y not one int8 [1, T, C] of C a multiple of 8", name);
            return false;
        }
        frames_ = x->shape[1];
        channels_ = x->shape[2];
        padded_ = padded(channels_);
        taps_ = taps->get_size() / padded_;
        if (taps->get_size() != taps_ * padded_ || taps_ < 3 || taps_ % 2 == 0 ||
            bias->get_size() != padded_ || !aligned(taps->get_element_ptr()) ||
            !aligned(bias->get_element_ptr())) {
            ESP_LOGE(TAG, "%s: taps or bias not laid for an odd kernel over %d channels", name, channels_);
            return false;
        }
        scratch_bytes_ = static_cast<size_t>(2 * frames_ + taps_ - 1) * padded_;
        if (!scratch_holds(scratch_bytes_)) {
            ESP_LOGE(TAG, "%s: no memory for %d padded rows", name, 2 * frames_ + taps_ - 1);
            return false;
        }
        bias->reset_bias_layout(quant_type, true);
        run_ = {frames_, padded_ / kChannelStep, taps_, shift_, 0};
        built_ = true;
        return true;
    }

    int shift_;
    int frames_ = 0;
    int channels_ = 0;
    int padded_ = 0;
    int taps_ = 0;
    size_t scratch_bytes_ = 0; // padded rows, then the output rows
    std::array<int32_t, kRunSize> run_ = {};
    bool built_ = false;
};

} // namespace

dl::module::Module *time_depthwise_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return TimeDepthwise::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
