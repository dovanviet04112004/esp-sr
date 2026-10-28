#include "core/espdl/ops/espdl_band_chain.hpp"

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
void ai_engine_band_k1_s8_esp32s3(int8_t *out, const int32_t *run);
void ai_engine_band_k1_res_s8_esp32s3(int8_t *out, const int32_t *run);
}

namespace ai {

namespace {

const char *TAG = "band_chain";

constexpr size_t kRank = 4;
constexpr int kChannels = 16;   // a map's values a pixel, one vector
constexpr int kInChannels = 12; // the fast kernels' weight rows a tap
constexpr int kParams = 4;      // a block's weights and bias, twice
constexpr int kShifts = 3;      // a block's two mac shifts and its residual's
constexpr int kColumns = 3;     // a block's input, middle and output columns
constexpr size_t kVectorAlignBytes = 16;
constexpr size_t kStreamSlackBytes = 32; // two weight rows the kernel reads past a column
constexpr int kTableWords = 3;           // a band's first input offset, first weight offset, taps
constexpr int kWindow = -1;              // the delay of an op running one window a call

enum Param { kW1 = 0, kB1, kW2, kB2 };
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
    kFastWeights = kFastColumns + kColumns,
    kFastSize = kFastWeights + kColumns
};

struct Scratch {
    int8_t *mem;
    size_t bytes;
};
// Weights and columns of a BandChain run without the loan; grows only while graphs are built.
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

int32_t word_of(const void *p) noexcept
{
    return static_cast<int32_t>(reinterpret_cast<uintptr_t>(p));
}

// One block of the chain: its two convolutions' runs and its weights' place in the scratch.
struct Block {
    std::array<int32_t, kFastSize> first;
    std::array<int32_t, kFastSize> second;
    size_t weight_bytes;
    size_t weights_at; // the first convolution's, the second's one stride later
    size_t stride;
};

// Inputs as esp_ppq_patches.band_chain_from lays them: x [1, B, T, 16], then each block's two convolutions'
// weights [1][band tap][12][16] and bias [16], in turn.
class BandChain final : public dl::module::Module {
  public:
    BandChain(const char *name, std::vector<int> band_taps, std::vector<int> dilations,
              std::vector<int> shifts, int delay)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), band_taps_(std::move(band_taps)),
          dilations_(std::move(dilations)), shifts_(std::move(shifts)), delay_(delay)
    {}

    ~BandChain() override
    {
        heap_caps_free(tables_);
    }

    void reset() override
    {
        seen_ = 0;
    }

    BandChain(const BandChain &) = delete;
    BandChain &operator=(const BandChain &) = delete;

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        return {input_shapes[0]};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[0]);
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        if (!built_ && !build(context, x, y)) { return; }
        const Loan lent = loan_lent();
        int8_t *mem = lent.bytes >= scratch_bytes_ ? lent.mem : s_scratch.mem;
        int8_t *columns = mem + weights_bytes_;
        for (size_t i = 0; i < blocks_.size(); i++) {
            Block &b = blocks_[i];
            int8_t *w1 = mem + b.weights_at;
            int8_t *w2 = w1 + b.stride;
            memcpy(w1, param(context, i, kW1)->get_element_ptr(), b.weight_bytes);
            memcpy(w2, param(context, i, kW2)->get_element_ptr(), b.weight_bytes);
            b.first[kFastWeights] = word_of(w1);
            b.second[kFastWeights] = word_of(w2);
            b.first[kFastBias] = word_of(param(context, i, kB1)->get_element_ptr());
            b.second[kFastBias] = word_of(param(context, i, kB2)->get_element_ptr());
        }
        const int8_t *xs = x->get_element_ptr<int8_t>();
        int8_t *ys = y->get_element_ptr<int8_t>();
        const int row = frames_ * kChannels;
        // Frames earlier than the stream come out zeros, as the window's padding gives them.
        const int first = delay_ == kWindow ? 0 : seen_ - delay_;
        for (int t = 0; t < frames_; t++) {
            if (first + t < 0) {
                for (int band = 0; band < bands_; band++) {
                    memset(ys + band * row + t * kChannels, 0, kChannels);
                }
                continue;
            }
            int8_t *in = columns;
            int8_t *middle = in + column_bytes_;
            int8_t *out = middle + column_bytes_;
            for (int band = 0; band < bands_; band++) {
                memcpy(in + band * kChannels, xs + band * row + t * kChannels, kChannels);
            }
            for (Block &b : blocks_) {
                b.first[kFastColumns] = word_of(in);
                ai_engine_band_k1_s8_esp32s3(middle, b.first.data());
                b.second[kFastColumns] = word_of(middle);
                b.second[kFastResidualDelta] = word_of(in) - word_of(out);
                ai_engine_band_k1_res_s8_esp32s3(out, b.second.data());
                std::swap(in, out);
            }
            for (int band = 0; band < bands_; band++) {
                memcpy(ys + band * row + t * kChannels, in + band * kChannels, kChannels);
            }
        }
        if (delay_ != kWindow) { seen_ = std::min(seen_ + frames_, delay_); }
    }

    void print() override
    {
        ESP_LOGI(TAG, "band taps: %s, dilations: %s, shifts: %s, quant_type: %s.",
                 dl::vector_to_string(band_taps_).c_str(), dl::vector_to_string(dilations_).c_str(),
                 dl::vector_to_string(shifts_).c_str(), dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        std::vector<int> band_taps;
        std::vector<int> dilations;
        std::vector<int> shifts;
        int in_channels = 0;
        int delay = 0;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        const bool streams = fbs_model->get_operation_attribute(node_name, "delay", delay) == ESP_OK;
        fbs_model->get_operation_attribute(node_name, "band_taps", band_taps);
        fbs_model->get_operation_attribute(node_name, "dilations", dilations);
        fbs_model->get_operation_attribute(node_name, "shifts", shifts);
        fbs_model->get_operation_attribute(node_name, "in_channels", in_channels);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        bool fits = quant_type == dl::QUANT_TYPE_SYMM_8BIT && in_channels == kInChannels &&
                    !band_taps.empty() && dilations.size() == band_taps.size() &&
                    shifts.size() == kShifts * band_taps.size() && (!streams || delay >= 0);
        for (size_t i = 0; fits && i < band_taps.size(); i++) {
            fits = band_taps[i] % 2 == 1 && dilations[i] >= 1 && shifts[kShifts * i] >= 1 &&
                   shifts[kShifts * i + 1] >= 1 && shifts[kShifts * i + 2] >= 0;
        }
        if (!fits) {
            ESP_LOGE(TAG,
                     "%s: only int8 single-frame blocks of odd band kernels and 12 inputs run as BandChain",
                     node_name.c_str());
            return nullptr;
        }
        return new BandChain(node_name.c_str(), std::move(band_taps), std::move(dilations), std::move(shifts),
                             streams ? delay : kWindow);
    }

  private:
    dl::TensorBase *param(dl::ModelContext *context, size_t block, int which) const noexcept
    {
        return context->get_tensor(m_inputs_index[1 + block * kParams + which]);
    }

    // Each band's first tap within the map, as offsets into an internal column.
    void fill_table(int32_t *table, int taps, int dilation) const noexcept
    {
        const int half = taps / 2;
        for (int b = 0; b < bands_; b++) {
            const int lo = std::max(0, half - b / dilation);
            const int hi = std::min(taps - 1, half + (bands_ - 1 - b) / dilation);
            table[b * kTableWords] = (b + dilation * (lo - half)) * kChannels;
            table[b * kTableWords + 1] = lo * kInChannels * kChannels;
            table[b * kTableWords + 2] = hi - lo + 1;
        }
    }

    bool build(dl::ModelContext *context, dl::TensorBase *x, dl::TensorBase *y)
    {
        if (x->shape.size() != kRank || x->shape[0] != 1 || x->shape[3] != kChannels ||
            x->shape != y->shape || x->get_dtype() != dl::DATA_TYPE_INT8 ||
            y->get_dtype() != dl::DATA_TYPE_INT8 ||
            m_inputs_index.size() != 1 + kParams * band_taps_.size()) {
            ESP_LOGE(TAG, "%s: x and y not one int8 [1, B, T, 16], or not 4 inputs a block", name);
            return false;
        }
        bands_ = x->shape[1];
        frames_ = x->shape[2];
        column_bytes_ = aligned_up(static_cast<size_t>(bands_) * kChannels);
        tables_ = static_cast<int32_t *>(
            heap_caps_malloc(band_taps_.size() * bands_ * kTableWords * sizeof(int32_t), MALLOC_CAP_SPIRAM));
        if (tables_ == nullptr) {
            ESP_LOGE(TAG, "%s: no memory for its band tables", name);
            return false;
        }
        blocks_.assign(band_taps_.size(), Block{});
        weights_bytes_ = 0;
        for (size_t i = 0; i < band_taps_.size(); i++) {
            Block &b = blocks_[i];
            b.weight_bytes = static_cast<size_t>(band_taps_[i]) * kInChannels * kChannels;
            b.stride = aligned_up(b.weight_bytes + kStreamSlackBytes);
            b.weights_at = weights_bytes_;
            weights_bytes_ += 2 * b.stride;
            for (int which : {kW1, kW2}) {
                if (param(context, i, which)->get_bytes() != b.weight_bytes) {
                    ESP_LOGE(TAG, "%s: block %u's weights not laid [1][%d][12][16]", name, (unsigned)i,
                             band_taps_[i]);
                    return false;
                }
            }
            for (int which : {kB1, kB2}) {
                dl::TensorBase *bias = param(context, i, which);
                if (bias->get_size() != kChannels) {
                    ESP_LOGE(TAG, "%s: block %u's bias not of 16 outputs", name, (unsigned)i);
                    return false;
                }
                bias->reset_bias_layout(quant_type, true);
            }
            int32_t *table = tables_ + i * bands_ * kTableWords;
            fill_table(table, band_taps_[i], dilations_[i]);
            const int residual = shifts_[kShifts * i + 2];
            b.first = {bands_, word_of(table), dilations_[i] * kChannels, kChannels,
                       shifts_[kShifts * i] - 1};
            b.second = {
                bands_, word_of(table), dilations_[i] * kChannels, kChannels, shifts_[kShifts * i + 1] - 1,
                0,      residual - 1};
        }
        scratch_bytes_ = weights_bytes_ + kColumns * column_bytes_;
        if (!scratch_holds(scratch_bytes_)) {
            ESP_LOGE(TAG, "%s: no memory for its weights and columns", name);
            return false;
        }
        loan_ask(scratch_bytes_);
        built_ = true;
        return true;
    }

    std::vector<int> band_taps_;
    std::vector<int> dilations_;
    std::vector<int> shifts_;
    int delay_;    // frames behind the graph input, or kWindow
    int seen_ = 0; // frames taken since the reset, held once past delay_
    int bands_ = 0;
    int frames_ = 0;
    size_t column_bytes_ = 0;
    size_t weights_bytes_ = 0;  // every block's weights, each with the kernel's slack
    size_t scratch_bytes_ = 0;  // the weights, then the input, middle and output columns
    int32_t *tables_ = nullptr; // a band table a block, in PSRAM
    std::vector<Block> blocks_;
    bool built_ = false;
};

} // namespace

dl::module::Module *band_chain_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return BandChain::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
