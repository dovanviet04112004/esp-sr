#include "core/espdl/ops/espdl_weighted_stats.hpp"

#include <algorithm>
#include <array>
#include <climits>
#include <cstdint>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "core/espdl/runtime/espdl_loan.hpp"
#include "core/espdl/ops/espdl_softmax_quant.hpp"
#include "dl_define.hpp"
#include "dl_module_base.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "fbs_model.hpp"

extern "C" {
void ai_engine_weighted_stats_row_s8_esp32s3(int16_t *sums, const int8_t *x, const int8_t *p,
                                             const int32_t *run);
}

namespace ai {

namespace {

const char *TAG = "weighted_stats";

constexpr int kRank = 3;
constexpr int kStep = 16;
constexpr size_t kAlignBytes = 16;
constexpr int kShifts = 5;
// Sums of a group as the kernel lays them: p * x for 16 channels, then p * x * x.
constexpr int kGroupSums = 2 * kStep;
// Frames whose int8 products sum within int16, as the kernel holds them.
constexpr int kMaxFrames = INT16_MAX / 128;
constexpr int kGroup = 64;            // channels softmaxed at once: a 64-byte line of a row
constexpr int kNoSoftmax = INT32_MIN; // prob_exponent when p comes in as it is

enum Input { kWeights = 0, kX, kInputs };
enum Shift { kShiftSquare = 0, kShiftWeighted, kShiftWeightedSquare, kShiftSum, kShiftSquareSum };

// esp-dl's tool::shift_and_round_half_up then its int8 truncation.
int8_t shift_round(int32_t value, int shift) noexcept
{
    int32_t shifted = shift > 0 ? (value + (1 << (shift - 1))) >> shift : value << -shift;
    return static_cast<int8_t>(
        std::max(std::min(shifted, static_cast<int32_t>(INT8_MAX)), static_cast<int32_t>(INT8_MIN)));
}

// Inputs as esp_ppq_patches.weighted_stats_from writes them: weights p, or the scores softmax_into took them
// from, and map x, [1, T, C] each; outputs the sums over T of both products, [1, C] each.
class WeightedStats final : public dl::module::Module {
  public:
    WeightedStats(const char *name, std::vector<int> shifts, int prob_exponent)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), shifts_(std::move(shifts)),
          prob_exponent_(prob_exponent)
    {}

    ~WeightedStats() override
    {
        heap_caps_free(sums_);
        heap_caps_free(table_);
        heap_caps_free(probs_);
    }

    WeightedStats(const WeightedStats &) = delete;
    WeightedStats &operator=(const WeightedStats &) = delete;

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        const std::vector<int> &x = input_shapes[kX];
        const std::vector<int> each = x.size() == kRank ? std::vector<int>{x[0], x[2]} : x;
        return {each, each};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *p = context->get_tensor(m_inputs_index[kWeights]);
        dl::TensorBase *x = context->get_tensor(m_inputs_index[kX]);
        if (!built_ && !build(p, x)) { return; }
        memset(sums_, 0, groups_ * kGroupSums * sizeof(int16_t));
        const int8_t *ps = p->get_element_ptr<int8_t>();
        const int8_t *xs = x->get_element_ptr<int8_t>();
        if (prob_exponent_ != kNoSoftmax) {
            softmax_groups(ps, xs);
        } else {
            for (int t = 0; t < frames_; t++) {
                ai_engine_weighted_stats_row_s8_esp32s3(sums_, xs + t * channels_, ps + t * channels_,
                                                        run_.data());
            }
        }
        int8_t *weighted = context->get_tensor(m_outputs_index[0])->get_element_ptr<int8_t>();
        int8_t *squared = context->get_tensor(m_outputs_index[1])->get_element_ptr<int8_t>();
        for (int c = 0; c < channels_; c++) {
            const int16_t *group = sums_ + (c / kStep) * kGroupSums + c % kStep;
            weighted[c] = shift_round(group[0], shifts_[kShiftSum]);
            squared[c] = shift_round(group[kStep], shifts_[kShiftSquareSum]);
        }
    }

    void print() override
    {
        ESP_LOGI(TAG, "shifts: %s, prob exponent: %d, quant_type: %s.", dl::vector_to_string(shifts_).c_str(),
                 prob_exponent_, dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        std::vector<int> shifts;
        int prob_exponent = kNoSoftmax;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "shifts", shifts);
        fbs_model->get_operation_attribute(node_name, "prob_exponent", prob_exponent);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || shifts.size() != kShifts ||
            *std::min_element(shifts.begin(), shifts.begin() + kShiftSum) < 1) {
            ESP_LOGE(TAG, "%s: only int8 products of shifts 1 or more run as WeightedStats",
                     node_name.c_str());
            return nullptr;
        }
        return new WeightedStats(node_name.c_str(), std::move(shifts), prob_exponent);
    }

  private:
    static bool aligned(const void *ptr) noexcept
    {
        return reinterpret_cast<uintptr_t>(ptr) % kAlignBytes == 0;
    }

    bool build(dl::TensorBase *p, dl::TensorBase *x)
    {
        if (m_inputs_index.size() != kInputs || x->shape.size() != kRank || x->shape != p->shape ||
            x->shape[0] != 1 || x->shape[1] > kMaxFrames || x->shape[2] % kStep != 0 ||
            x->get_dtype() != dl::DATA_TYPE_INT8 || p->get_dtype() != dl::DATA_TYPE_INT8 ||
            !aligned(x->get_element_ptr()) || !aligned(p->get_element_ptr())) {
            ESP_LOGE(TAG, "%s: p and x not one int8 [1, T, C] of C a multiple of 16 and T up to %d", name,
                     kMaxFrames);
            return false;
        }
        frames_ = x->shape[1];
        channels_ = x->shape[2];
        groups_ = channels_ / kStep;
        sums_ = static_cast<int16_t *>(
            heap_caps_aligned_calloc(kAlignBytes, groups_ * kGroupSums, sizeof(int16_t), MALLOC_CAP_SPIRAM));
        if (sums_ == nullptr) {
            ESP_LOGE(TAG, "%s: no memory for %d sums", name, groups_ * kGroupSums);
            return false;
        }
        run_ = {groups_, shifts_[kShiftSquare], shifts_[kShiftWeighted], shifts_[kShiftWeightedSquare]};
        if (prob_exponent_ != kNoSoftmax && !build_softmax(p)) { return false; }
        built_ = true;
        return true;
    }

    bool build_softmax(dl::TensorBase *scores)
    {
        table_ = static_cast<float *>(
            heap_caps_malloc((kSoftmaxTableSize + kGroup) * sizeof(float), MALLOC_CAP_SPIRAM));
        probs_bytes_ = static_cast<size_t>(frames_) * kGroup;
        probs_ = static_cast<int8_t *>(heap_caps_aligned_alloc(kAlignBytes, probs_bytes_, MALLOC_CAP_SPIRAM));
        if (table_ == nullptr || probs_ == nullptr ||
            !softmax_table_s8(table_, scores->get_exponent(), nullptr)) {
            ESP_LOGE(TAG, "%s: no memory for the softmax table and %d rows of probabilities", name, frames_);
            return false;
        }
        scales_ = table_ + kSoftmaxTableSize;
        out_rescale_ = DL_RESCALE(prob_exponent_);
        return true;
    }

    // Each group of 64 channels: its probabilities over the frames as SoftmaxQuant gives them, into the loan
    // when it holds them, then weighed in while those rows are cached.
    void softmax_groups(const int8_t *scores, const int8_t *xs) noexcept
    {
        const Loan lent = loan_lent();
        int8_t *probs = lent.bytes >= probs_bytes_ ? lent.mem : probs_;
        std::array<int32_t, 4> run = run_;
        for (int g = 0; g < channels_; g += kGroup) {
            const int columns = std::min(kGroup, channels_ - g);
            softmax_columns_s8(probs, kGroup, scores + g, frames_, channels_, columns, table_, scales_,
                               out_rescale_);
            run[0] = columns / kStep;
            for (int t = 0; t < frames_; t++) {
                ai_engine_weighted_stats_row_s8_esp32s3(
                    sums_ + g / kStep * kGroupSums, xs + t * channels_ + g, probs + t * kGroup, run.data());
            }
        }
    }

    std::vector<int> shifts_;
    int prob_exponent_;
    int frames_ = 0;
    int channels_ = 0;
    int groups_ = 0;
    int16_t *sums_ = nullptr;
    std::array<int32_t, 4> run_ = {};
    float *table_ = nullptr; // esp-dl's exp table, then a group's column scales
    float *scales_ = nullptr;
    float out_rescale_ = 0.0f;
    int8_t *probs_ = nullptr; // a group's [T][64] probabilities, without a loan
    size_t probs_bytes_ = 0;
    bool built_ = false;
};

} // namespace

dl::module::Module *weighted_stats_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return WeightedStats::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
