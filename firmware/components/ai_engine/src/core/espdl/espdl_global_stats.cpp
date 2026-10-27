#include "core/espdl/espdl_global_stats.hpp"

#include <algorithm>
#include <array>
#include <climits>
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
void ai_engine_row_sums_s8_esp32s3(int16_t *sums, const int8_t *x, int groups);
void ai_engine_centred_squares_row_s8_esp32s3(int16_t *sums, const int8_t *x, const int8_t *mean,
                                              const int32_t *run);
}

namespace ai {

namespace {

const char *TAG = "global_stats";

constexpr int kRank = 3;
constexpr int kStep = 16;
constexpr size_t kAlignBytes = 16;
constexpr int kShifts = 2;
// Frames whose int8 values sum within int16, as the kernels hold them.
constexpr int kMaxFrames = INT16_MAX / 128;

enum Shift { kShiftRequantize = 0, kShiftSquare };

int8_t clip(int32_t value) noexcept
{
    return static_cast<int8_t>(
        std::max(std::min(value, static_cast<int32_t>(INT8_MAX)), static_cast<int32_t>(INT8_MIN)));
}

// esp-dl's int8 ReduceMean: the int32 sum times the input scale, over the count, times the output's rescale,
// then tool::round, floor.s of the float plus 0.5, and the int8 truncation.
int8_t mean_like_espdl(int32_t sum, int count, int in_exponent, int out_exponent) noexcept
{
    const float v = static_cast<float>(sum) * DL_SCALE(in_exponent) / static_cast<float>(count) *
                    DL_RESCALE(out_exponent);
    int whole;
    asm("floor.s %0, %1, 0" : "=a"(whole) : "f"(v + 0.5f));
    return clip(whole);
}

// Input as esp_ppq_patches.global_stats_from writes it: x [1, T, C]; outputs the mean [1, 1, C] and the mean
// of the squares of x less the requantised mean [1, 1, C]. "shifts": the requantisation's, the square's.
class GlobalStats final : public dl::module::Module {
  public:
    GlobalStats(const char *name, std::vector<int> shifts, int square_exponent)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), shifts_(std::move(shifts)),
          square_exponent_(square_exponent)
    {}

    ~GlobalStats() override
    {
        heap_caps_free(sums_);
    }

    GlobalStats(const GlobalStats &) = delete;
    GlobalStats &operator=(const GlobalStats &) = delete;

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        const std::vector<int> &x = input_shapes[0];
        const std::vector<int> each = x.size() == kRank ? std::vector<int>{x[0], 1, x[2]} : x;
        return {each, each};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[0]);
        dl::TensorBase *mean = context->get_tensor(m_outputs_index[0]);
        dl::TensorBase *spread = context->get_tensor(m_outputs_index[1]);
        if (!built_ && !build(x)) { return; }
        const int8_t *xs = x->get_element_ptr<int8_t>();
        int8_t *means = mean->get_element_ptr<int8_t>();
        memset(sums_, 0, channels_ * sizeof(int16_t));
        for (int t = 0; t < frames_; t++) {
            ai_engine_row_sums_s8_esp32s3(sums_, xs + t * channels_, groups_);
        }
        const int shift = shifts_[kShiftRequantize];
        for (int c = 0; c < channels_; c++) {
            means[c] = mean_like_espdl(sums_[c], frames_, x->get_exponent(), mean->get_exponent());
            centre_[c] = clip(shift > 0 ? (means[c] + (1 << (shift - 1))) >> shift : means[c]);
        }
        memset(sums_, 0, channels_ * sizeof(int16_t));
        for (int t = 0; t < frames_; t++) {
            ai_engine_centred_squares_row_s8_esp32s3(sums_, xs + t * channels_, centre_, run_.data());
        }
        int8_t *spreads = spread->get_element_ptr<int8_t>();
        for (int c = 0; c < channels_; c++) {
            spreads[c] = mean_like_espdl(sums_[c], frames_, square_exponent_, spread->get_exponent());
        }
    }

    void print() override
    {
        ESP_LOGI(TAG, "shifts: %s, square exponent: %d, quant_type: %s.",
                 dl::vector_to_string(shifts_).c_str(), square_exponent_,
                 dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        std::vector<int> shifts;
        int square_exponent = 0;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "shifts", shifts);
        fbs_model->get_operation_attribute(node_name, "square_exponent", square_exponent);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || shifts.size() != kShifts ||
            shifts[kShiftRequantize] < 0 || shifts[kShiftSquare] < 1) {
            ESP_LOGE(
                TAG,
                "%s: only an int8 pool whose mean requantises right and squares shift 1 or more runs here",
                node_name.c_str());
            return nullptr;
        }
        return new GlobalStats(node_name.c_str(), std::move(shifts), square_exponent);
    }

  private:
    bool build(dl::TensorBase *x)
    {
        if (x->shape.size() != kRank || x->shape[0] != 1 || x->shape[1] > kMaxFrames ||
            x->shape[2] % kStep != 0 || x->get_dtype() != dl::DATA_TYPE_INT8 ||
            reinterpret_cast<uintptr_t>(x->get_element_ptr()) % kAlignBytes) {
            ESP_LOGE(TAG, "%s: x not one int8 [1, T, C] of C a multiple of 16 and T up to %d", name,
                     kMaxFrames);
            return false;
        }
        frames_ = x->shape[1];
        channels_ = x->shape[2];
        groups_ = channels_ / kStep;
        // The int16 sums, then the requantised means.
        sums_ = static_cast<int16_t *>(
            heap_caps_aligned_calloc(kAlignBytes, channels_, sizeof(int16_t) + 1, MALLOC_CAP_SPIRAM));
        if (sums_ == nullptr) {
            ESP_LOGE(TAG, "%s: no memory for %d sums", name, channels_);
            return false;
        }
        centre_ = reinterpret_cast<int8_t *>(sums_ + channels_);
        run_ = {groups_, shifts_[kShiftSquare]};
        built_ = true;
        return true;
    }

    std::vector<int> shifts_;
    int square_exponent_;
    int frames_ = 0;
    int channels_ = 0;
    int groups_ = 0;
    int16_t *sums_ = nullptr;
    int8_t *centre_ = nullptr;
    std::array<int32_t, 2> run_ = {};
    bool built_ = false;
};

} // namespace

dl::module::Module *global_stats_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return GlobalStats::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
