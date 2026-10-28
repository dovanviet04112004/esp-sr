#include "core/espdl/ops/espdl_band_head.hpp"

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
void ai_engine_band_k1_linear_s8_esp32s3(int8_t *out, const int32_t *run);
void ai_engine_band_k3_s8_esp32s3(int8_t *out, const int32_t *run);
}

namespace ai {

namespace {

const char *TAG = "band_head";

constexpr size_t kRank = 4;
constexpr int kChannels = 16;   // a map's values a pixel, one vector
constexpr int kInChannels = 12; // band_s8_esp32s3.S's weight rows a tap
constexpr int kHeadTaps = 3;    // bands and frames
constexpr int kHeadBelow = 1;   // bands padded below the lowest
constexpr int kSpreadTaps = 5;
constexpr int kSpreadBelow = 2;
constexpr size_t kVectorAlignBytes = 16;
constexpr size_t kStreamSlackBytes = 32; // two weight rows the kernel reads past a column
constexpr int kTableWords = 3;           // a band's first input offset, first weight offset, taps
constexpr int kInt8Min = -128;
constexpr int kInt8Max = 127;
constexpr int kKept = kHeadTaps - 1; // input columns a streaming call takes from the last
constexpr int kWindow = -1;          // the delay of an op running one window a call

enum Input { kX = 0, kHeadW, kHeadB, kSpreadW, kSpreadB, kPitchW, kPitchB };
enum Output { kLogits = 0, kPeaks };
enum Shift { kShiftHead = 0, kShiftSpread, kShiftPitch, kShifts };
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
    kFastWeights = kFastColumns + kHeadTaps,
    kFastSize = kFastWeights + kHeadTaps
};

struct Scratch {
    int8_t *mem;
    size_t bytes;
};
// Weights and columns of a BandHead run without the loan; grows only while graphs are built.
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

// Round half up as tie728_s8_vector_round_result does, saturated to int8; shift 1 or more.
int8_t rounded(int32_t acc, int shift) noexcept
{
    const int32_t value = ((acc >> (shift - 1)) + 1) >> 1;
    return static_cast<int8_t>(std::clamp<int32_t>(value, kInt8Min, kInt8Max));
}

// Each output band's first tap within in_bands, input band b - below + tap, as offsets into a column whose
// bands lie in_stride bytes apart.
void fill_table(int32_t *table, int out_bands, int in_bands, int below, int taps, int in_stride) noexcept
{
    for (int b = 0; b < out_bands; b++) {
        const int lo = std::max(0, below - b);
        const int hi = std::min(taps - 1, in_bands - 1 - b + below);
        table[b * kTableWords] = (b - below + lo) * in_stride;
        table[b * kTableWords + 1] = lo * kInChannels * kChannels;
        table[b * kTableWords + 2] = hi - lo + 1;
    }
}

// Inputs as esp_ppq_patches.band_head_from lays them: x [1, B, T, 16]; the 3x3 convolution's weights
// [3][3][12][16] and bias [16]; the 5x1 one's [1][5][12][16] and [16]; the 1x1 one's [1][N] and [1].
class BandHead final : public dl::module::Module {
  public:
    BandHead(const char *name, std::vector<int> shifts, int bands, int delay)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), shifts_(std::move(shifts)),
          bands_(bands), delay_(delay)
    {}

    ~BandHead() override
    {
        heap_caps_free(tables_);
        heap_caps_free(kept_x_);
    }

    void reset() override
    {
        seen_ = 0;
        if (kept_x_ != nullptr) { memset(kept_x_, 0, static_cast<size_t>(bands_) * frames_ * kChannels); }
    }

    BandHead(const BandHead &) = delete;
    BandHead &operator=(const BandHead &) = delete;

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        const std::vector<int> &x = input_shapes[kX];
        const std::vector<int> &pitch = input_shapes[kPitchW];
        return {{1, bands_ - 1, x[2], 1}, {1, x[2], pitch.back()}};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[kX]);
        dl::TensorBase *logits = context->get_tensor(m_outputs_index[kLogits]);
        dl::TensorBase *peaks = context->get_tensor(m_outputs_index[kPeaks]);
        if (!built_ && !build(context, x, logits, peaks)) { return; }
        const Loan lent = loan_lent();
        int8_t *mem = lent.bytes >= scratch_bytes_ ? lent.mem : s_scratch.mem;
        int8_t *head_w = mem;
        int8_t *spread_w = head_w + head_stride_;
        int8_t *head = spread_w + spread_stride_;
        int8_t *spread = head + column_bytes_;
        memcpy(head_w, context->get_tensor(m_inputs_index[kHeadW])->get_element_ptr(), head_bytes_);
        memcpy(spread_w, context->get_tensor(m_inputs_index[kSpreadW])->get_element_ptr(), spread_bytes_);
        head_run_[kRunBias] = word_of(context->get_tensor(m_inputs_index[kHeadB])->get_element_ptr());
        spread_run_[kRunBias] = word_of(context->get_tensor(m_inputs_index[kSpreadB])->get_element_ptr());
        head_fast_[kFastBias] = head_run_[kRunBias];
        spread_fast_[kFastBias] = spread_run_[kRunBias];
        for (int c = 0; c < kHeadTaps; c++) {
            head_fast_[kFastWeights + c] = word_of(head_w + c * head_run_[kRunTapBytes]);
        }
        spread_fast_[kFastWeights] = word_of(spread_w);
        spread_fast_[kFastColumns] = word_of(head);
        const int8_t *pitch_w = context->get_tensor(m_inputs_index[kPitchW])->get_element_ptr<int8_t>();
        const int32_t pitch_b = context->get_tensor(m_inputs_index[kPitchB])->get_element_ptr<int32_t>()[0];
        const int8_t *xs = x->get_element_ptr<int8_t>();
        int8_t *logit = logits->get_element_ptr<int8_t>();
        int8_t *peak = peaks->get_element_ptr<int8_t>();
        const int out_bands = bands_ - 1;
        const int8_t *columns[kHeadTaps];
        const bool streams = delay_ != kWindow;
        for (int t = 0; t < frames_; t++) {
            if (streams && seen_ - delay_ + t < 0) {
                zero_frame(logit, peak, t);
                continue;
            }
            // A streaming call's column t is centred on input column t - 1, the window's on t.
            const int first = t - kHeadTaps / 2 - (streams ? 1 : 0);
            if (streams) {
                for (int c = 0; c < kHeadTaps; c++) {
                    head_fast_[kFastColumns + c] = word_of(input_column(xs, first + c));
                }
                ai_engine_band_k3_s8_esp32s3(head, head_fast_.data());
            } else if (first >= 0 && first + kHeadTaps <= frames_) {
                for (int c = 0; c < kHeadTaps; c++) {
                    head_fast_[kFastColumns + c] = word_of(xs + (first + c) * kChannels);
                }
                ai_engine_band_k3_s8_esp32s3(head, head_fast_.data());
            } else {
                for (int c = 0; c < kHeadTaps; c++) {
                    const int at = first + c;
                    columns[c] = at >= 0 && at < frames_ ? xs + at * kChannels : nullptr;
                }
                ai_engine_band_conv_s8_esp32s3(head, columns, head_w, head_run_.data());
            }
            ai_engine_band_k1_linear_s8_esp32s3(spread, spread_fast_.data());
            for (int c = 0; c < outputs_; c++) {
                int8_t most = spread[c];
                for (int b = 1; b < out_bands; b++) {
                    most = std::max(most, spread[b * kChannels + c]);
                }
                peak[t * outputs_ + c] = most;
            }
            for (int b = 0; b < out_bands; b++) {
                int32_t acc = pitch_b;
                for (int c = 0; c < outputs_; c++) {
                    acc += head[b * kChannels + c] * pitch_w[c];
                }
                logit[b * frames_ + t] = rounded(acc, shifts_[kShiftPitch]);
            }
        }
        if (streams) { keep(xs); }
    }

    void print() override
    {
        ESP_LOGI(TAG, "bands: %d, shifts: %s, quant_type: %s.", bands_, dl::vector_to_string(shifts_).c_str(),
                 dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        std::vector<int> shifts;
        int bands = 0;
        int delay = 0;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "shifts", shifts);
        fbs_model->get_operation_attribute(node_name, "bands", bands);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        const bool streams = fbs_model->get_operation_attribute(node_name, "delay", delay) == ESP_OK;
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || shifts.size() != kShifts ||
            *std::min_element(shifts.begin(), shifts.end()) < 1 || bands < kSpreadTaps ||
            (streams && delay < 1)) {
            ESP_LOGE(TAG,
                     "%s: only an int8 head of shifts 1 or more over enough bands, any delay 1 or more, runs "
                     "as BandHead",
                     node_name.c_str());
            return nullptr;
        }
        return new BandHead(node_name.c_str(), std::move(shifts), bands, streams ? delay : kWindow);
    }

  private:
    // Input column t of a streaming call; a negative t is the last call's column, at its place in kept_x_.
    const int8_t *input_column(const int8_t *xs, int t) const noexcept
    {
        return t >= 0 ? xs + t * kChannels : kept_x_ + (frames_ + t) * kChannels;
    }

    void zero_frame(int8_t *logit, int8_t *peak, int t) const noexcept
    {
        for (int b = 0; b < bands_ - 1; b++) {
            logit[b * frames_ + t] = 0;
        }
        memset(peak + t * outputs_, 0, outputs_);
    }

    // The last kKept input columns of the bands the head reads, for the next call; then the frames taken.
    void keep(const int8_t *xs) noexcept
    {
        const int row = frames_ * kChannels;
        for (int b = 0; b < bands_; b++) {
            const int at = b * row + (frames_ - kKept) * kChannels;
            memcpy(kept_x_ + at, xs + at, kKept * kChannels);
        }
        seen_ = std::min(seen_ + frames_, delay_);
    }

    bool build(dl::ModelContext *context, dl::TensorBase *x, dl::TensorBase *logits, dl::TensorBase *peaks)
    {
        dl::TensorBase *pitch_w = context->get_tensor(m_inputs_index[kPitchW]);
        if (x->shape.size() != kRank || x->shape[0] != 1 || x->shape[3] != kChannels ||
            x->shape[1] < bands_ || x->get_dtype() != dl::DATA_TYPE_INT8 || !aligned(x->get_element_ptr())) {
            ESP_LOGE(TAG, "%s: x not one aligned int8 [1, B, T, 16] of %d bands or more", name, bands_);
            return false;
        }
        frames_ = x->shape[2];
        outputs_ = pitch_w->get_size();
        head_bytes_ = static_cast<size_t>(kHeadTaps) * kHeadTaps * kInChannels * kChannels;
        spread_bytes_ = static_cast<size_t>(kSpreadTaps) * kInChannels * kChannels;
        if (outputs_ < 1 || outputs_ > kChannels || logits->get_size() != (bands_ - 1) * frames_ ||
            peaks->get_size() != frames_ * outputs_ ||
            context->get_tensor(m_inputs_index[kHeadW])->get_bytes() != head_bytes_ ||
            context->get_tensor(m_inputs_index[kSpreadW])->get_bytes() != spread_bytes_ ||
            context->get_tensor(m_inputs_index[kPitchB])->get_size() != 1) {
            ESP_LOGE(TAG, "%s: weights or outputs not laid as band_head_from lays them", name);
            return false;
        }
        for (int i : {kHeadB, kSpreadB}) {
            dl::TensorBase *bias = context->get_tensor(m_inputs_index[i]);
            if (bias->get_size() != kChannels) {
                ESP_LOGE(TAG, "%s: a bias not of 16 outputs", name);
                return false;
            }
            bias->reset_bias_layout(quant_type, true);
        }
        const int out_bands = bands_ - 1;
        head_stride_ = aligned_up(head_bytes_ + kStreamSlackBytes);
        spread_stride_ = aligned_up(spread_bytes_ + kStreamSlackBytes);
        column_bytes_ = aligned_up(static_cast<size_t>(out_bands) * kChannels);
        scratch_bytes_ = head_stride_ + spread_stride_ + 2 * column_bytes_;
        tables_ = static_cast<int32_t *>(
            heap_caps_malloc(2 * out_bands * kTableWords * sizeof(int32_t), MALLOC_CAP_SPIRAM));
        if (delay_ != kWindow) {
            kept_x_ = static_cast<int8_t *>(heap_caps_aligned_calloc(
                kVectorAlignBytes, static_cast<size_t>(bands_) * frames_, kChannels, MALLOC_CAP_SPIRAM));
        }
        if (tables_ == nullptr || !scratch_holds(scratch_bytes_) ||
            (delay_ != kWindow && kept_x_ == nullptr)) {
            ESP_LOGE(TAG, "%s: no memory for its band tables, weights and columns", name);
            return false;
        }
        const int row = frames_ * kChannels;
        int32_t *spread_table = tables_ + out_bands * kTableWords;
        fill_table(tables_, out_bands, bands_, kHeadBelow, kHeadTaps, row);
        fill_table(spread_table, out_bands, out_bands, kSpreadBelow, kSpreadTaps, kChannels);
        const int32_t head_tap_bytes = kHeadTaps * kInChannels * kChannels;
        head_run_ = {out_bands,
                     word_of(tables_),
                     kHeadTaps,
                     head_tap_bytes,
                     row,
                     kChannels,
                     shifts_[kShiftHead],
                     0,
                     0,
                     0,
                     1};
        spread_run_ = {out_bands,
                       word_of(spread_table),
                       1,
                       kSpreadTaps * kInChannels * kChannels,
                       kChannels,
                       kChannels,
                       shifts_[kShiftSpread],
                       0,
                       0,
                       0,
                       0};
        head_fast_[kFastBands] = out_bands;
        head_fast_[kFastTable] = head_run_[kRunTable];
        head_fast_[kFastStep] = head_run_[kRunStep];
        head_fast_[kFastOutStride] = head_run_[kRunOutStride];
        head_fast_[kFastShift] = shifts_[kShiftHead] - 1;
        spread_fast_[kFastBands] = out_bands;
        spread_fast_[kFastTable] = spread_run_[kRunTable];
        spread_fast_[kFastStep] = spread_run_[kRunStep];
        spread_fast_[kFastOutStride] = spread_run_[kRunOutStride];
        spread_fast_[kFastShift] = shifts_[kShiftSpread] - 1;
        loan_ask(scratch_bytes_);
        built_ = true;
        return true;
    }

    std::vector<int> shifts_;
    int bands_;
    int delay_;                // frames behind the graph input, or kWindow
    int seen_ = 0;             // frames taken since the reset, held once past delay_
    int8_t *kept_x_ = nullptr; // the last call's last input columns, laid as x
    int frames_ = 0;
    int outputs_ = 0;
    size_t head_bytes_ = 0;
    size_t spread_bytes_ = 0;
    size_t head_stride_ = 0;   // the 3x3 weights, the kernel's slack and padding
    size_t spread_stride_ = 0; // the 5x1 weights, likewise
    size_t column_bytes_ = 0;
    size_t scratch_bytes_ = 0;  // both weights, then the 3x3 column, then the 5x1 one
    int32_t *tables_ = nullptr; // the 3x3 then the 5x1 band table, in PSRAM
    std::array<int32_t, kRunSize> head_run_ = {};
    std::array<int32_t, kRunSize> spread_run_ = {};
    std::array<int32_t, kFastSize> head_fast_ = {};
    std::array<int32_t, kFastSize> spread_fast_ = {};
    bool built_ = false;
};

} // namespace

dl::module::Module *band_head_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return BandHead::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
