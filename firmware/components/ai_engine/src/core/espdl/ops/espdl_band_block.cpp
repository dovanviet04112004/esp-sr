#include "core/espdl/ops/espdl_band_block.hpp"

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
void ai_engine_band_conv_s8_esp32s3(int8_t *out, const int8_t *const *columns, const int8_t *weights,
                                    const int32_t *run);
void ai_engine_band_k1_s8_esp32s3(int8_t *out, const int32_t *run);
void ai_engine_band_k1_res_s8_esp32s3(int8_t *out, const int32_t *run);
void ai_engine_band_k3_s8_esp32s3(int8_t *out, const int32_t *run);
void ai_engine_band_k3_res_s8_esp32s3(int8_t *out, const int32_t *run);
}

namespace ai {

namespace {

const char *TAG = "band_block";

constexpr size_t kRank = 4;
constexpr int kChannels = 16;   // a map's values a pixel, one vector
constexpr int kInChannels = 12; // band_s8_esp32s3.S's products a tap
constexpr int kTimeTapsMax = 3;
constexpr size_t kVectorAlignBytes = 16;
constexpr size_t kStreamSlackBytes = 32; // two weight rows the kernel reads past a column
constexpr int kTableWords = 3;           // a band's first input offset, first weight offset, taps
constexpr int kWindow = -1;              // the delay of an op running one window a call

enum Input { kX = 0, kW1, kB1, kW2, kB2 };
enum Shift { kShift1 = 0, kShift2, kShiftResidual, kShifts };
// Slots of band_s8_esp32s3.S's run.
enum Run {
    kRunBands = 0,
    kRunTable,
    kRunTimeTaps,
    kRunTapBytes,
    kRunStep,
    kRunOutStride,
    kRunShift,
    kRunBias,
    kRunResidual,
    kRunResidualDelta,
    kRunRelu,
    kRunSize
};
// Slots of the fast kernels' run, for a column whose time taps all lie within the frames.
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
    kFastWeights = kFastColumns + kTimeTapsMax,
    kFastSize = kFastWeights + kTimeTapsMax
};
using FastKernel = void (*)(int8_t *out, const int32_t *run);

struct Scratch {
    int8_t *mem;
    size_t bytes;
};
// Weights and middle columns of a BandBlock run without the loan; grows only while graphs are built.
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

// Inputs as esp_ppq_patches.band_block_from lays them: x [1, B, T, 16], each convolution's weights
// [time tap][band tap][12][16] and bias [16]; a column is a frame, its bands a pixel or a row apart.
class BandBlock final : public dl::module::Module {
  public:
    BandBlock(const char *name, int band_taps, int time_taps, int dilation, std::vector<int> shifts,
              int delay)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), band_taps_(band_taps),
          time_taps_(time_taps), dilation_(dilation), shifts_(std::move(shifts)), delay_(delay)
    {}

    ~BandBlock() override
    {
        heap_caps_free(tables_);
        heap_caps_free(kept_x_);
        heap_caps_free(kept_middle_);
    }

    void reset() override
    {
        seen_ = 0;
        if (kept_x_ != nullptr) { memset(kept_x_, 0, static_cast<size_t>(bands_) * frames_ * kChannels); }
        if (kept_middle_ != nullptr) { memset(kept_middle_, 0, (time_taps_ - 1) * column_bytes()); }
    }

    BandBlock(const BandBlock &) = delete;
    BandBlock &operator=(const BandBlock &) = delete;

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        return {input_shapes[kX]};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[kX]);
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        if (!built_ && !build(context, x, y)) { return; }
        const Loan lent = loan_lent();
        int8_t *mem = lent.bytes >= scratch_bytes_ ? lent.mem : s_scratch.mem;
        int8_t *w1 = mem;
        int8_t *w2 = w1 + weight_stride_;
        ring_ = w2 + weight_stride_;
        memcpy(w1, context->get_tensor(m_inputs_index[kW1])->get_element_ptr(), weight_bytes_);
        memcpy(w2, context->get_tensor(m_inputs_index[kW2])->get_element_ptr(), weight_bytes_);
        xs_ = x->get_element_ptr<int8_t>();
        int8_t *ys = y->get_element_ptr<int8_t>();
        first_[kRunBias] = word_of(context->get_tensor(m_inputs_index[kB1])->get_element_ptr());
        second_[kRunBias] = word_of(context->get_tensor(m_inputs_index[kB2])->get_element_ptr());
        second_[kRunResidualDelta] = word_of(xs_) - word_of(ys);
        first_fast_[kFastBias] = first_[kRunBias];
        second_fast_[kFastBias] = second_[kRunBias];
        second_fast_[kFastResidualDelta] = second_[kRunResidualDelta];
        for (int c = 0; c < time_taps_; c++) {
            first_fast_[kFastWeights + c] = word_of(w1 + c * first_[kRunTapBytes]);
            second_fast_[kFastWeights + c] = word_of(w2 + c * second_[kRunTapBytes]);
        }
        if (delay_ != kWindow) {
            stream(ys);
            return;
        }
        const int half = time_taps_ / 2;
        for (int t = 0; t < half && t < frames_; t++) {
            middle(t, w1);
        }
        const int8_t *columns[kTimeTapsMax];
        for (int t = 0; t < frames_; t++) {
            if (t + half < frames_) { middle(t + half, w1); }
            int8_t *out = ys + t * kChannels;
            if (t >= half && t + half < frames_) {
                for (int c = 0; c < time_taps_; c++) {
                    second_fast_[kFastColumns + c] = word_of(middle_column(t - half + c));
                }
                second_kernel_(out, second_fast_.data());
                continue;
            }
            for (int c = 0; c < time_taps_; c++) {
                const int at = t - half + c;
                columns[c] = at >= 0 && at < frames_ ? middle_column(at) : nullptr;
            }
            ai_engine_band_conv_s8_esp32s3(out, columns, w2, second_.data());
        }
    }

    void print() override
    {
        ESP_LOGI(TAG, "kernel: %d x %d, dilation: %d, shifts: %s, quant_type: %s.", band_taps_, time_taps_,
                 dilation_, dl::vector_to_string(shifts_).c_str(), dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        std::vector<int> kernel;
        std::vector<int> shifts;
        int dilation = 0;
        int in_channels = 0;
        int delay = 0;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "kernel", kernel);
        fbs_model->get_operation_attribute(node_name, "dilation", dilation);
        fbs_model->get_operation_attribute(node_name, "shifts", shifts);
        fbs_model->get_operation_attribute(node_name, "in_channels", in_channels);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        const bool streams = fbs_model->get_operation_attribute(node_name, "delay", delay) == ESP_OK;
        const bool kernel_fits =
            kernel.size() == 2 && kernel[0] % 2 == 1 && (kernel[1] == 1 || kernel[1] == 3);
        const bool shifts_fit = shifts.size() == kShifts && shifts[kShift1] >= 1 && shifts[kShift2] >= 1 &&
                                shifts[kShiftResidual] >= 0;
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || !kernel_fits || dilation < 1 || !shifts_fit ||
            in_channels != kInChannels || (streams && delay < kernel[1] - 1)) {
            ESP_LOGE(TAG,
                     "%s: only int8 blocks of an odd band kernel, 1 or 3 frames, 12 inputs, any delay past "
                     "their own run as BandBlock",
                     node_name.c_str());
            return nullptr;
        }
        return new BandBlock(node_name.c_str(), kernel[0], kernel[1], dilation, std::move(shifts),
                             streams ? delay : kWindow);
    }

  private:
    size_t column_bytes() const noexcept
    {
        return static_cast<size_t>(bands_) * kChannels;
    }

    int8_t *middle_column(int t) const noexcept
    {
        return ring_ + (t % time_taps_ + time_taps_) % time_taps_ * column_bytes();
    }

    // Input column t of a streaming call; a negative t is the last call's column, at its place in kept_x_.
    const int8_t *input_column(int t) const noexcept
    {
        return t >= 0 ? xs_ + t * kChannels : kept_x_ + (frames_ + t) * kChannels;
    }

    // Output column t is centred on input column t - 2 half; middle columns under -half are the last call's.
    void stream(int8_t *ys) noexcept
    {
        const int half = time_taps_ / 2;
        const int lead = 2 * half;
        for (int k = 0; k < lead; k++) {
            memcpy(middle_column(k - 3 * half), kept_middle_ + k * column_bytes(), column_bytes());
        }
        const int input_first = seen_ - (delay_ - lead);
        const int output_first = seen_ - delay_;
        const int row = frames_ * kChannels;
        for (int t = 0; t < frames_; t++) {
            stream_middle(t - half, input_first + t - half);
            int8_t *out = ys + t * kChannels;
            if (output_first + t < 0) {
                for (int b = 0; b < bands_; b++) {
                    memset(out + b * row, 0, kChannels);
                }
                continue;
            }
            for (int c = 0; c < time_taps_; c++) {
                second_fast_[kFastColumns + c] = word_of(middle_column(t - 3 * half + c));
            }
            second_fast_[kFastResidualDelta] = word_of(input_column(t - lead)) - word_of(out);
            second_kernel_(out, second_fast_.data());
        }
        for (int k = 0; k < lead; k++) {
            memcpy(kept_middle_ + k * column_bytes(), middle_column(frames_ - 3 * half + k), column_bytes());
        }
        for (int b = 0; b < bands_; b++) {
            const int at = b * row + (frames_ - lead) * kChannels;
            memcpy(kept_x_ + at, xs_ + at, lead * kChannels);
        }
        seen_ = std::min(seen_ + frames_, delay_);
    }

    // Middle column t of a streaming call, the first convolution's with ReLU; zeros where no frame is.
    void stream_middle(int t, int frame) noexcept
    {
        if (frame < 0) {
            memset(middle_column(t), 0, column_bytes());
            return;
        }
        const int half = time_taps_ / 2;
        for (int c = 0; c < time_taps_; c++) {
            first_fast_[kFastColumns + c] = word_of(input_column(t - half + c));
        }
        first_kernel_(middle_column(t), first_fast_.data());
    }

    // The first convolution's column t, ReLU on, into the ring.
    void middle(int t, const int8_t *w1) noexcept
    {
        const int8_t *columns[kTimeTapsMax];
        const int half = time_taps_ / 2;
        if (t >= half && t + half < frames_) {
            for (int c = 0; c < time_taps_; c++) {
                first_fast_[kFastColumns + c] = word_of(xs_ + (t - half + c) * kChannels);
            }
            first_kernel_(middle_column(t), first_fast_.data());
            return;
        }
        for (int c = 0; c < time_taps_; c++) {
            const int at = t - half + c;
            columns[c] = at >= 0 && at < frames_ ? xs_ + at * kChannels : nullptr;
        }
        ai_engine_band_conv_s8_esp32s3(middle_column(t), columns, w1, first_.data());
    }

    // Each band's first tap within the map, as offsets into a column whose bands lie in_stride bytes apart.
    void fill_table(int32_t *table, int in_stride) const noexcept
    {
        const int half = band_taps_ / 2;
        for (int b = 0; b < bands_; b++) {
            const int lo = std::max(0, half - b / dilation_);
            const int hi = std::min(band_taps_ - 1, half + (bands_ - 1 - b) / dilation_);
            table[b * kTableWords] = (b + dilation_ * (lo - half)) * in_stride;
            table[b * kTableWords + 1] = lo * kInChannels * kChannels;
            table[b * kTableWords + 2] = hi - lo + 1;
        }
    }

    bool build(dl::ModelContext *context, dl::TensorBase *x, dl::TensorBase *y)
    {
        if (x->shape.size() != kRank || x->shape[0] != 1 || x->shape[3] != kChannels ||
            x->shape != y->shape || x->get_dtype() != dl::DATA_TYPE_INT8 ||
            y->get_dtype() != dl::DATA_TYPE_INT8 || !aligned(x->get_element_ptr()) ||
            !aligned(y->get_element_ptr())) {
            ESP_LOGE(TAG, "%s: x and y not one aligned int8 [1, B, T, 16]", name);
            return false;
        }
        bands_ = x->shape[1];
        frames_ = x->shape[2];
        weight_bytes_ = static_cast<size_t>(time_taps_) * band_taps_ * kInChannels * kChannels;
        for (int i : {kW1, kW2}) {
            if (context->get_tensor(m_inputs_index[i])->get_bytes() != weight_bytes_) {
                ESP_LOGE(TAG, "%s: weights not laid [%d][%d][12][16]", name, time_taps_, band_taps_);
                return false;
            }
        }
        for (int i : {kB1, kB2}) {
            dl::TensorBase *bias = context->get_tensor(m_inputs_index[i]);
            if (bias->get_size() != kChannels) {
                ESP_LOGE(TAG, "%s: a bias not of 16 outputs", name);
                return false;
            }
            bias->reset_bias_layout(quant_type, true);
        }
        weight_stride_ = aligned_up(weight_bytes_ + kStreamSlackBytes);
        scratch_bytes_ = 2 * weight_stride_ + static_cast<size_t>(time_taps_) * bands_ * kChannels;
        if (!scratch_holds(scratch_bytes_)) {
            ESP_LOGE(TAG, "%s: no memory for its weights and %d middle columns", name, time_taps_);
            return false;
        }
        if (delay_ != kWindow && time_taps_ > 1) {
            kept_x_ = static_cast<int8_t *>(heap_caps_aligned_calloc(
                kVectorAlignBytes, static_cast<size_t>(bands_) * frames_, kChannels, MALLOC_CAP_SPIRAM));
            kept_middle_ =
                static_cast<int8_t *>(heap_caps_calloc(time_taps_ - 1, column_bytes(), MALLOC_CAP_SPIRAM));
            if (kept_x_ == nullptr || kept_middle_ == nullptr) {
                ESP_LOGE(TAG, "%s: no memory for the columns it keeps between calls", name);
                return false;
            }
        }
        tables_ = static_cast<int32_t *>(
            heap_caps_malloc(2 * bands_ * kTableWords * sizeof(int32_t), MALLOC_CAP_SPIRAM));
        if (tables_ == nullptr) {
            ESP_LOGE(TAG, "%s: no memory for its band tables", name);
            return false;
        }
        const int row = frames_ * kChannels;
        fill_table(tables_, row);
        fill_table(tables_ + bands_ * kTableWords, kChannels);
        const int32_t tap_bytes = band_taps_ * kInChannels * kChannels;
        first_ = {bands_,    word_of(tables_), time_taps_, tap_bytes, dilation_ * row,
                  kChannels, shifts_[kShift1], 0,          0,         0,
                  1};
        second_ = {bands_,
                   word_of(tables_ + bands_ * kTableWords),
                   time_taps_,
                   tap_bytes,
                   dilation_ * kChannels,
                   row,
                   shifts_[kShift2],
                   0,
                   shifts_[kShiftResidual] + 1,
                   0,
                   1};
        first_fast_[kFastBands] = bands_;
        first_fast_[kFastTable] = first_[kRunTable];
        first_fast_[kFastStep] = first_[kRunStep];
        first_fast_[kFastOutStride] = first_[kRunOutStride];
        first_fast_[kFastShift] = shifts_[kShift1] - 1;
        second_fast_[kFastBands] = bands_;
        second_fast_[kFastTable] = second_[kRunTable];
        second_fast_[kFastStep] = second_[kRunStep];
        second_fast_[kFastOutStride] = second_[kRunOutStride];
        second_fast_[kFastShift] = shifts_[kShift2] - 1;
        second_fast_[kFastResidual] = shifts_[kShiftResidual] - 1;
        const bool three = time_taps_ == kTimeTapsMax;
        first_kernel_ = three ? ai_engine_band_k3_s8_esp32s3 : ai_engine_band_k1_s8_esp32s3;
        second_kernel_ = three ? ai_engine_band_k3_res_s8_esp32s3 : ai_engine_band_k1_res_s8_esp32s3;
        loan_ask(scratch_bytes_);
        built_ = true;
        return true;
    }

    int band_taps_;
    int time_taps_;
    int dilation_;
    std::vector<int> shifts_;
    int delay_;                     // frames behind the graph input, or kWindow
    int seen_ = 0;                  // frames taken since the reset, held once past delay_
    int8_t *kept_x_ = nullptr;      // the last call's last input columns, laid as x
    int8_t *kept_middle_ = nullptr; // and its last middle columns
    int bands_ = 0;
    int frames_ = 0;
    size_t weight_bytes_ = 0;
    size_t weight_stride_ = 0;  // a convolution's weights, the kernel's slack and padding
    size_t scratch_bytes_ = 0;  // both weights, then the ring of middle columns
    int32_t *tables_ = nullptr; // each convolution's band table, in PSRAM
    std::array<int32_t, kRunSize> first_ = {};
    std::array<int32_t, kRunSize> second_ = {};
    std::array<int32_t, kFastSize> first_fast_ = {};
    std::array<int32_t, kFastSize> second_fast_ = {};
    FastKernel first_kernel_ = nullptr;
    FastKernel second_kernel_ = nullptr;
    const int8_t *xs_ = nullptr;
    int8_t *ring_ = nullptr;
    bool built_ = false;
};

} // namespace

dl::module::Module *band_block_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return BandBlock::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
