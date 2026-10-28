#include "core/espdl/espdl_stage_sum.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "core/espdl/espdl_loan.hpp"
#include "dl_module_base.hpp"
#include "esp_log.h"
#include "fbs_model.hpp"

extern "C" void ai_engine_stage_sum_row_s8_esp32s3(int8_t *row, const int8_t *x, const int8_t *weights,
                                                   const int32_t *run);

namespace ai {

namespace {

const char *TAG = "stage_sum";

constexpr int kRank = 3;
constexpr int kChannelStep = 16;
constexpr size_t kVectorAlignBytes = 16;
constexpr size_t kRunWords = 3;

using Run = std::array<int32_t, kRunWords>;

bool aligned(dl::TensorBase *tensor) noexcept
{
    return reinterpret_cast<uintptr_t>(tensor->get_element_ptr()) % kVectorAlignBytes == 0;
}

struct Term {
    int map;     // input index
    int weights; // input index
    int rows;    // each row of map stands for this many rows of the sum
    Run run;
};

// Inputs as esp_ppq_patches.stage_sum_from writes them: map and weights of each term in the order the chain
// added them; "rows" says how many times a Resize the patch took out repeated each map's rows.
class StageSum final : public dl::module::Module {
  public:
    StageSum(const char *name, std::vector<int> rows)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), rows_(std::move(rows))
    {}

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        std::vector<int> y = input_shapes[0];
        if (y.size() == kRank && !rows_.empty()) { y[1] *= rows_[0]; }
        return {y};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        if (!built_ && !build(context, y)) { return; }
        for (size_t k = 0; k < terms_.size(); k++) {
            maps_[k] = context->get_tensor(terms_[k].map)->get_element_ptr<int8_t>();
            weights_[k] = context->get_tensor(terms_[k].weights)->get_element_ptr<int8_t>();
        }
        int8_t *out = y->get_element_ptr<int8_t>();
        const int channels = y->shape[2];
        const Loan lent = loan_lent();
        if (lent.bytes >= terms_.size() * channels) {
            for (size_t k = 0; k < terms_.size(); k++) {
                memcpy(lent.mem + k * channels, weights_[k], channels);
                weights_[k] = lent.mem + k * channels;
            }
        }
        const int rows = y->shape[1];
        for (int t0 = 0; t0 < rows; t0 += block_) {
            const int end = std::min(t0 + block_, rows);
            for (size_t k = 0; k < terms_.size(); k++) {
                for (int t = t0; t < end; t++) {
                    const int8_t *x = maps_[k] + t / terms_[k].rows * channels;
                    ai_engine_stage_sum_row_s8_esp32s3(out + t * channels, x, weights_[k],
                                                       terms_[k].run.data());
                }
            }
        }
    }

    void print() override
    {
        ESP_LOGI(TAG, "rows: %s, quant_type: %s.", dl::vector_to_string(rows_).c_str(),
                 dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        std::vector<int> rows;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "rows", rows);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || rows.size() < 2) {
            ESP_LOGE(TAG, "%s: only an int8 sum of two terms or more runs as StageSum", node_name.c_str());
            return nullptr;
        }
        return new StageSum(node_name.c_str(), std::move(rows));
    }

  private:
    bool build(dl::ModelContext *context, dl::TensorBase *y)
    {
        if (m_inputs_index.size() != 2 * rows_.size() || y->shape.size() != kRank ||
            y->shape[2] % kChannelStep != 0 || !aligned(y)) {
            ESP_LOGE(TAG, "%s: %u inputs for %u terms, or the sum is not [N, T, C] on 16-byte vectors", name,
                     static_cast<unsigned>(m_inputs_index.size()), static_cast<unsigned>(rows_.size()));
            return false;
        }
        for (size_t k = 0; k < rows_.size(); k++) {
            dl::TensorBase *map = context->get_tensor(m_inputs_index[2 * k]);
            dl::TensorBase *weights = context->get_tensor(m_inputs_index[2 * k + 1]);
            const int shift = y->get_exponent() - map->get_exponent() - weights->get_exponent();
            if (map->shape.size() != kRank || map->shape[2] != y->shape[2] || rows_[k] < 1 ||
                map->shape[1] * rows_[k] != y->shape[1] || weights->get_size() != y->shape[2] || shift < 0 ||
                !aligned(map) || !aligned(weights)) {
                ESP_LOGE(TAG, "%s: term %u does not fit the sum or shifts left by %d", name,
                         static_cast<unsigned>(k), -shift);
                return false;
            }
            terms_.push_back({m_inputs_index[2 * k],
                              m_inputs_index[2 * k + 1],
                              rows_[k],
                              {y->shape[2] / kChannelStep, shift, k == 0 ? 1 : 0}});
            block_ = std::max(block_, rows_[k]);
        }
        maps_.resize(terms_.size());
        weights_.resize(terms_.size());
        built_ = true;
        return true;
    }

    std::vector<int> rows_;
    std::vector<Term> terms_;
    std::vector<const int8_t *> maps_;
    std::vector<const int8_t *> weights_;
    int block_ = 1; // output rows a term sums into at once: largest repeat
    bool built_ = false;
};

} // namespace

dl::module::Module *stage_sum_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return StageSum::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
