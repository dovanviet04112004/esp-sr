#include "core/espdl/ops/espdl_band_stem.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "core/espdl/runtime/espdl_loan.hpp"
#include "dl_module_base.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "fbs_model.hpp"

extern "C" {
void ai_engine_band_k3_l3_s8_esp32s3(int8_t *out, const int32_t *run);
}

namespace ai {

namespace {

const char *TAG = "band_stem";

constexpr size_t kRank = 4;
constexpr int kChannels = 16;    // an output pixel's values, one vector
constexpr int kLanes = 3;        // inputs a pixel of ai_engine_band_k3_l3_s8_esp32s3
constexpr int kTaps = 3;         // bands and frames
constexpr int kKept = kTaps - 1; // laid columns a streaming call takes from the last
constexpr size_t kVectorAlignBytes = 16;
constexpr size_t kStreamSlackBytes = 32; // two weight rows the kernel reads past a column
constexpr int kTableWords = 3;           // a band's first input offset, first weight offset, taps
constexpr int kWindow = -1;              // the delay of an op running one window a call

enum Input { kX = 0, kWeights, kBias };
// Slots of the fast kernels' run.
enum Fast {
    kFastBands = 0,
    kFastTable,
    kFastStep,
    kFastOutStride,
    kFastShift,
    kFastBias,
    kFastResidual,
    kFastResidualDelta,
    kFastColumns,
    kFastWeights = kFastColumns + kTaps,
    kFastSize = kFastWeights + kTaps
};

struct Scratch {
    int8_t *mem;
    size_t bytes;
};
// Weights and laid-out columns of a BandStem run without the loan; grows only while graphs are built.
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

size_t aligned_up(size_t bytes) noexcept
{
    return (bytes + kVectorAlignBytes - 1) / kVectorAlignBytes * kVectorAlignBytes;
}

bool aligned(const void *p) noexcept
{
    return reinterpret_cast<uintptr_t>(p) % kVectorAlignBytes == 0;
}

int32_t word_of(const void *p) noexcept
{
    return static_cast<int32_t>(reinterpret_cast<uintptr_t>(p));
}

// Inputs as esp_ppq_patches.band_stem_from lays them: x [1, B, T, 3], weights [3][3][3][16], bias [16].
// With a delay it streams (KEHOACH 3.11 step 3.2): output frame t of a call is input frame t - 1.
class BandStem final : public dl::module::Module {
  public:
    BandStem(const char *name, int shift, int delay)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), shift_(shift), delay_(delay)
    {}

    ~BandStem() override
    {
        heap_caps_free(table_);
        heap_caps_free(kept_);
    }

    void reset() override
    {
        seen_ = 0;
        if (kept_ != nullptr) { memset(kept_, 0, kKept * column_bytes_); }
    }

    BandStem(const BandStem &) = delete;
    BandStem &operator=(const BandStem &) = delete;

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        const std::vector<int> &x = input_shapes[kX];
        return {{x[0], x[1], x[2], kChannels}};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[kX]);
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        if (!built_ && !build(context, x, y)) { return; }
        const Loan lent = loan_lent();
        int8_t *mem = lent.bytes >= scratch_bytes_ ? lent.mem : s_scratch.mem;
        int8_t *weights = mem;
        int8_t *zeros = weights + weight_stride_;
        int8_t *ring = zeros + column_bytes_;
        memcpy(weights, context->get_tensor(m_inputs_index[kWeights])->get_element_ptr(), weight_bytes_);
        memset(zeros, 0, column_bytes_);
        fast_[kFastBias] = word_of(context->get_tensor(m_inputs_index[kBias])->get_element_ptr());
        for (int c = 0; c < kTaps; c++) {
            fast_[kFastWeights + c] = word_of(weights + c * kTaps * kLanes * kChannels);
        }
        const int8_t *xs = x->get_element_ptr<int8_t>();
        int8_t *ys = y->get_element_ptr<int8_t>();
        if (kept_ != nullptr) {
            stream(ring, xs, ys);
            return;
        }
        lay_out(ring, xs, 0);
        for (int t = 0; t < frames_; t++) {
            if (t + 1 < frames_) { lay_out(ring + (t + 1) % kTaps * column_bytes_, xs, t + 1); }
            for (int c = 0; c < kTaps; c++) {
                const int at = t - 1 + c;
                const int8_t *column = at >= 0 && at < frames_ ? ring + at % kTaps * column_bytes_ : zeros;
                fast_[kFastColumns + c] = word_of(column);
            }
            ai_engine_band_k3_l3_s8_esp32s3(ys + t * kChannels, fast_.data());
        }
    }

    void print() override
    {
        ESP_LOGI(TAG, "shift: %d, quant_type: %s.", shift_, dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        int shift = 0;
        int lanes = 0;
        int delay = 0;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "shift", shift);
        fbs_model->get_operation_attribute(node_name, "lanes", lanes);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        const bool streams = fbs_model->get_operation_attribute(node_name, "delay", delay) == ESP_OK;
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || shift < 1 || lanes != kLanes ||
            (streams && delay < 1)) {
            ESP_LOGE(TAG,
                     "%s: only an int8 stem of 3 inputs, a shift and any delay of 1 or more runs as BandStem",
                     node_name.c_str());
            return nullptr;
        }
        return new BandStem(node_name.c_str(), shift, streams ? delay : kWindow);
    }

  private:
    int8_t *slot(int8_t *ring, int t) const noexcept
    {
        return ring + (t % kTaps + kTaps) % kTaps * column_bytes_;
    }

    // Output column t reads laid columns t - 2 to t; columns -2 and -1 are the last call's, zeros after a
    // reset.
    void stream(int8_t *ring, const int8_t *xs, int8_t *ys) noexcept
    {
        for (int k = 0; k < kKept; k++) {
            memcpy(slot(ring, k - kKept), kept_ + k * column_bytes_, column_bytes_);
        }
        const int first = seen_ - delay_;
        for (int t = 0; t < frames_; t++) {
            lay_out(slot(ring, t), xs, t);
            if (first + t < 0) {
                for (int b = 0; b < bands_; b++) {
                    memset(ys + (b * frames_ + t) * kChannels, 0, kChannels);
                }
                continue;
            }
            for (int c = 0; c < kTaps; c++) {
                fast_[kFastColumns + c] = word_of(slot(ring, t - kKept + c));
            }
            ai_engine_band_k3_l3_s8_esp32s3(ys + t * kChannels, fast_.data());
        }
        for (int k = 0; k < kKept; k++) {
            memcpy(kept_ + k * column_bytes_, slot(ring, frames_ - kKept + k), column_bytes_);
        }
        seen_ = std::min(seen_ + frames_, delay_);
    }

    // Frame t's column of x, its 3 values a band, laid 16 bytes a band at column; lanes 3 to 15 go unread.
    void lay_out(int8_t *column, const int8_t *xs, int t) const noexcept
    {
        const int8_t *from = xs + t * kLanes;
        for (int b = 0; b < bands_; b++) {
            memcpy(column + b * kChannels, from + b * frames_ * kLanes, kLanes);
        }
    }

    bool build(dl::ModelContext *context, dl::TensorBase *x, dl::TensorBase *y)
    {
        if (x->shape.size() != kRank || x->shape[0] != 1 || x->shape[3] != kLanes ||
            x->get_dtype() != dl::DATA_TYPE_INT8 || y->get_dtype() != dl::DATA_TYPE_INT8 ||
            y->shape != std::vector<int>{1, x->shape[1], x->shape[2], kChannels} ||
            !aligned(y->get_element_ptr())) {
            ESP_LOGE(TAG, "%s: x not int8 [1, B, T, 3] or y not one aligned int8 [1, B, T, 16]", name);
            return false;
        }
        bands_ = x->shape[1];
        frames_ = x->shape[2];
        weight_bytes_ = static_cast<size_t>(kTaps) * kTaps * kLanes * kChannels;
        dl::TensorBase *bias = context->get_tensor(m_inputs_index[kBias]);
        if (context->get_tensor(m_inputs_index[kWeights])->get_bytes() != weight_bytes_ ||
            bias->get_size() != kChannels) {
            ESP_LOGE(TAG, "%s: weights not laid [3][3][3][16] or a bias not of 16 outputs", name);
            return false;
        }
        bias->reset_bias_layout(quant_type, true);
        weight_stride_ = aligned_up(weight_bytes_ + kStreamSlackBytes);
        column_bytes_ = aligned_up(static_cast<size_t>(bands_) * kChannels);
        scratch_bytes_ = weight_stride_ + (1 + kTaps) * column_bytes_;
        table_ = static_cast<int32_t *>(
            heap_caps_malloc(bands_ * kTableWords * sizeof(int32_t), MALLOC_CAP_SPIRAM));
        if (delay_ != kWindow) {
            kept_ = static_cast<int8_t *>(heap_caps_calloc(kKept, column_bytes_, MALLOC_CAP_SPIRAM));
        }
        if (table_ == nullptr || !scratch_holds(scratch_bytes_) || (delay_ != kWindow && kept_ == nullptr)) {
            ESP_LOGE(TAG, "%s: no memory for its band table, weights and columns", name);
            return false;
        }
        for (int b = 0; b < bands_; b++) {
            const int lo = std::max(0, 1 - b);
            const int hi = std::min(kTaps - 1, bands_ - b);
            table_[b * kTableWords] = (b - 1 + lo) * kChannels;
            table_[b * kTableWords + 1] = lo * kLanes * kChannels;
            table_[b * kTableWords + 2] = hi - lo + 1;
        }
        fast_[kFastBands] = bands_;
        fast_[kFastTable] = word_of(table_);
        fast_[kFastStep] = kChannels;
        fast_[kFastOutStride] = frames_ * kChannels;
        fast_[kFastShift] = shift_ - 1;
        loan_ask(scratch_bytes_);
        built_ = true;
        return true;
    }

    int shift_;
    int delay_;    // frames behind the graph input, or kWindow
    int seen_ = 0; // frames taken since the reset, held once past delay_
    int bands_ = 0;
    int frames_ = 0;
    size_t weight_bytes_ = 0;
    size_t weight_stride_ = 0; // the weights, the kernel's slack and padding
    size_t column_bytes_ = 0;
    size_t scratch_bytes_ = 0; // weights, a column of zeros, then the column ring
    int32_t *table_ = nullptr; // in PSRAM
    int8_t *kept_ = nullptr;   // the last call's last laid columns, when streaming
    std::array<int32_t, kFastSize> fast_ = {};
    bool built_ = false;
};

} // namespace

dl::module::Module *band_stem_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return BandStem::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
