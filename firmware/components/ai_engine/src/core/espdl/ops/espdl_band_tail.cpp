#include "core/espdl/ops/espdl_band_tail.hpp"

#include <algorithm>
#include <cstdint>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "dl_module_base.hpp"
#include "esp_log.h"
#include "fbs_model.hpp"

namespace ai {

namespace {

const char *TAG = "band_tail";

constexpr int kTaps = 3;         // the hidden convolution's frames
constexpr int kKept = kTaps - 1; // joined columns a call takes from the last
constexpr int kHiddenMax = 16;
constexpr int kInt8Min = -128;
constexpr int kInt8Max = 127;

enum Input { kPeaks = 0, kLevel, kHiddenW, kHiddenB, kConfidenceW, kConfidenceB };
enum Shift { kShiftHidden = 0, kShiftConfidence, kShifts };

// Round half up as tie728_s8_vector_round_result does, saturated to int8; shift 1 or more.
int8_t rounded(int32_t acc, int shift) noexcept
{
    const int32_t value = ((acc >> (shift - 1)) + 1) >> 1;
    return static_cast<int8_t>(std::clamp<int32_t>(value, kInt8Min, kInt8Max));
}

// Inputs as esp_ppq_patches.band_tail_from lays them: peaks [1, T, C], level [1, 1, T], the hidden
// convolution's weights [3][C + 1][H] and bias [H], then the confidence one's [H] and [1]; output [1, T].
class BandTail final : public dl::module::Module {
  public:
    BandTail(const char *name, std::vector<int> shifts, int lag, int delay)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), shifts_(std::move(shifts)),
          lag_(lag), delay_(delay)
    {}

    BandTail(const BandTail &) = delete;
    BandTail &operator=(const BandTail &) = delete;

    void reset() override
    {
        seen_ = 0;
        std::fill(levels_.begin(), levels_.end(), 0);
        std::fill(joins_.begin(), joins_.end(), 0);
    }

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        return {{1, input_shapes[kPeaks][1]}};
    }

    // Output column t is frame t - delay of the call; it reads joined columns t to t + 2, the first two the
    // last call's. Joined column t holds the peaks of frame t - (delay - 1) and the level of the same frame.
    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *peaks = context->get_tensor(m_inputs_index[kPeaks]);
        dl::TensorBase *level = context->get_tensor(m_inputs_index[kLevel]);
        dl::TensorBase *out = context->get_tensor(m_outputs_index[0]);
        if (!built_ && !build(context, peaks, level, out)) { return; }
        const int8_t *p = peaks->get_element_ptr<int8_t>();
        const int8_t *hw = context->get_tensor(m_inputs_index[kHiddenW])->get_element_ptr<int8_t>();
        const int32_t *hb = context->get_tensor(m_inputs_index[kHiddenB])->get_element_ptr<int32_t>();
        const int8_t *cw = context->get_tensor(m_inputs_index[kConfidenceW])->get_element_ptr<int8_t>();
        const int32_t cb = context->get_tensor(m_inputs_index[kConfidenceB])->get_element_ptr<int32_t>()[0];
        int8_t *y = out->get_element_ptr<int8_t>();
        memcpy(levels_.data() + lag_, level->get_element_ptr<int8_t>(), frames_);
        const int joined_first = seen_ - (delay_ - 1);
        for (int t = 0; t < frames_; t++) {
            int8_t *join = joined(kKept + t);
            if (joined_first + t < 0) {
                memset(join, 0, width_);
                continue;
            }
            memcpy(join, p + t * (width_ - 1), width_ - 1);
            join[width_ - 1] = levels_[t];
        }
        for (int t = 0; t < frames_; t++) {
            if (seen_ - delay_ + t < 0) {
                y[t] = 0;
                continue;
            }
            int32_t acc = cb;
            for (int o = 0; o < hidden_; o++) {
                int32_t h = hb[o];
                for (int k = 0; k < kTaps; k++) {
                    const int8_t *join = joined(t + k);
                    for (int i = 0; i < width_; i++) {
                        h += join[i] * hw[(k * width_ + i) * hidden_ + o];
                    }
                }
                acc += std::max<int32_t>(rounded(h, shifts_[kShiftHidden]), 0) * cw[o];
            }
            y[t] = rounded(acc, shifts_[kShiftConfidence]);
        }
        memmove(levels_.data(), levels_.data() + frames_, lag_);
        memmove(joined(0), joined(frames_), kKept * width_);
        seen_ = std::min(seen_ + frames_, delay_);
    }

    void print() override
    {
        ESP_LOGI(TAG, "lag: %d, delay: %d, shifts: %s, quant_type: %s.", lag_, delay_,
                 dl::vector_to_string(shifts_).c_str(), dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        std::vector<int> shifts;
        int lag = 0;
        int delay = 0;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "shifts", shifts);
        fbs_model->get_operation_attribute(node_name, "lag", lag);
        fbs_model->get_operation_attribute(node_name, "delay", delay);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || shifts.size() != kShifts ||
            *std::min_element(shifts.begin(), shifts.end()) < 1 || lag < 0 || delay < 1) {
            ESP_LOGE(
                TAG,
                "%s: only an int8 tail of shifts 1 or more, a lag of 0 or more and a delay runs as BandTail",
                node_name.c_str());
            return nullptr;
        }
        return new BandTail(node_name.c_str(), std::move(shifts), lag, delay);
    }

  private:
    int8_t *joined(int column) noexcept
    {
        return joins_.data() + column * width_;
    }

    bool build(dl::ModelContext *context, dl::TensorBase *peaks, dl::TensorBase *level, dl::TensorBase *out)
    {
        if (peaks->shape.size() != 3 || peaks->shape[0] != 1 || peaks->get_dtype() != dl::DATA_TYPE_INT8 ||
            level->get_dtype() != dl::DATA_TYPE_INT8 || out->get_dtype() != dl::DATA_TYPE_INT8) {
            ESP_LOGE(TAG, "%s: peaks not int8 [1, T, C], or level or output not int8", name);
            return false;
        }
        frames_ = peaks->shape[1];
        width_ = peaks->shape[2] + 1;
        hidden_ = context->get_tensor(m_inputs_index[kHiddenB])->get_size();
        if (level->get_size() != frames_ || out->get_size() != frames_ || hidden_ < 1 ||
            hidden_ > kHiddenMax ||
            context->get_tensor(m_inputs_index[kHiddenW])->get_size() != kTaps * width_ * hidden_ ||
            context->get_tensor(m_inputs_index[kConfidenceW])->get_size() != hidden_ ||
            context->get_tensor(m_inputs_index[kConfidenceB])->get_size() != 1) {
            ESP_LOGE(TAG, "%s: level, output or weights not laid as band_tail_from lays them", name);
            return false;
        }
        levels_.assign(lag_ + frames_, 0);
        joins_.assign(static_cast<size_t>(kKept + frames_) * width_, 0);
        built_ = true;
        return true;
    }

    std::vector<int> shifts_;
    int lag_;      // frames the peaks trail the level
    int delay_;    // frames the output trails the graph input
    int seen_ = 0; // frames taken since the reset, held once past delay_
    int frames_ = 0;
    int width_ = 0; // a joined column's values: the peaks, then the level
    int hidden_ = 0;
    std::vector<int8_t> levels_; // the last lag_ levels, then this call's
    std::vector<int8_t> joins_;  // the last kKept joined columns, then this call's
    bool built_ = false;
};

} // namespace

dl::module::Module *band_tail_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return BandTail::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
