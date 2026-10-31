#include "core/espdl/ops/espdl_upsample_concat_conv.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "core/espdl/runtime/espdl_loan.hpp"
#include "dl_base_conv2d.hpp"
#include "dl_base_conv_args.hpp"
#include "dl_base_conv_select.hpp"
#include "dl_module_base.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "fbs_model.hpp"

extern "C" {
void ai_engine_lut_s8_esp32s3(int8_t *values, int n, const int8_t *turned);
}

namespace ai {

namespace {

const char *TAG = "upsample_concat_conv";

constexpr size_t kRank = 4;
constexpr int kTableBytes = 256;  // an int8 table's entries
constexpr int kTableMiddle = 128; // its entry for input 0
constexpr size_t kAlignBytes = 16;
constexpr int kLanes = 16;                  // output channels an int8 kernel group holds
constexpr int kLoanFilterBytes = 32 * 1024; // the most weights the op copies to the loan, as ConvLut

enum Input { kSmall = 0, kOther, kFilter, kBias };

struct Scratch {
    int8_t *mem;
    size_t bytes;
};
// The Concat's row of every UpsampleConcatConv run, shared; grows only while graphs are built.
Scratch s_row = {nullptr, 0};

bool row_holds(size_t bytes) noexcept
{
    if (bytes <= s_row.bytes) { return true; }
    heap_caps_free(s_row.mem);
    s_row.mem = static_cast<int8_t *>(heap_caps_aligned_alloc(kAlignBytes, bytes, MALLOC_CAP_SPIRAM));
    s_row.bytes = s_row.mem != nullptr ? bytes : 0;
    return s_row.mem != nullptr;
}

// Inputs as esp_ppq_patches.upsample_concat_conv writes them: the doubled map's input, the other map, the
// filter, the bias, then the table esp-dl appends; up_first 1 when the doubled map comes first in the Concat.
class UpsampleConcatConv final : public dl::module::Module {
  public:
    UpsampleConcatConv(const char *name, int conv_exponent, bool up_first)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), conv_exponent_(conv_exponent),
          up_first_(up_first)
    {}

    UpsampleConcatConv(const UpsampleConcatConv &) = delete;
    UpsampleConcatConv &operator=(const UpsampleConcatConv &) = delete;

    bool is_lut_module() const override
    {
        return true;
    }

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        std::vector<int> y = input_shapes[kOther];
        const std::vector<int> &filter = input_shapes[kFilter];
        if (y.size() == kRank && filter.size() == kRank) { y[3] = filter[3]; }
        return {y};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *small = context->get_tensor(m_inputs_index[kSmall]);
        dl::TensorBase *other = context->get_tensor(m_inputs_index[kOther]);
        dl::TensorBase *filter = context->get_tensor(m_inputs_index[kFilter]);
        dl::TensorBase *bias = context->get_tensor(m_inputs_index[kBias]);
        dl::TensorBase *table = context->get_tensor(m_inputs_index.back());
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        if (!laid_) {
            bias->reset_bias_layout(quant_type, false);
            const int8_t *t = table->get_element_ptr<int8_t>();
            memcpy(turned_.data(), t + kTableMiddle, kTableBytes - kTableMiddle);
            memcpy(turned_.data() + kTableBytes - kTableMiddle, t, kTableMiddle);
            loan_ask(std::min(filter->get_bytes(), kLoanFilterBytes));
            laid_ = true;
        }
        const int h = other->shape[1];
        const int w = other->shape[2];
        const int c_small = small->shape[3];
        const int c_other = other->shape[3];
        const int c = c_small + c_other;
        if (small->shape[1] * 2 != h || small->shape[2] * 2 != w || filter->shape[2] != c) {
            ESP_LOGE(TAG, "%s: maps %dx%d and %dx%d, %d channels, no doubled Concat", name, small->shape[1],
                     small->shape[2], h, w, filter->shape[2]);
            return;
        }
        if (!row_holds(static_cast<size_t>(w) * c)) {
            ESP_LOGE(TAG, "%s: no PSRAM for the Concat's row", name);
            return;
        }
        dl::TensorBase cat({1, h, w, c}, s_row.mem, other->exponent, dl::DATA_TYPE_INT8, false);
        const int table_exponent = y->exponent;
        y->exponent = conv_exponent_;
        std::vector<int> flat = {0, 0, 0, 0};
        std::vector<int> one = {1, 1};
        dl::base::ConvOpArgs args(y, &cat, flat, filter, one, one, 1, bias, dl::Linear, nullptr,
                                  dl::RUNTIME_MODE_SINGLE_CORE, quant_type);
        y->exponent = table_exponent;
        if (args.size() != 1 ||
            !dl::base::dl_conv_ensure_kernels(fn_, args.get_args(0), false, quant_type, TAG)) {
            ESP_LOGE(TAG, "%s: esp-dl has no kernel for this convolution", name);
            return;
        }
        const dl::base::ConvArgsType whole = args.get_args(0);
        const Loan lent = loan_lent();
        const int per_out = c;
        int tile = whole.output_channel;
        if (static_cast<size_t>(filter->get_bytes()) > lent.bytes && whole.n_remainder == 0 &&
            whole.mac_shift >= 0) {
            const int fit = static_cast<int>(lent.bytes) / per_out / kLanes * kLanes;
            tile = fit >= kLanes ? fit : whole.output_channel;
        }
        const int8_t *smalls = small->get_element_ptr<int8_t>();
        const int8_t *others = other->get_element_ptr<int8_t>();
        int8_t *ys = y->get_element_ptr<int8_t>();
        const int cy = y->shape[3];
        const size_t y_row = static_cast<size_t>(w) * cy;
        for (int c0 = 0; c0 < cy; c0 += tile) {
            dl::base::ConvArgsType part = whole;
            const int channels = std::min(tile, cy - c0);
            if (tile != cy) {
                part.n_div_x = channels / kLanes;
                part.output_channel = channels;
                part.filter_element = dl::base::conv_ptr_add(whole.filter_element, c0 * per_out, 1);
                part.bias_element = dl::base::conv_ptr_add(whole.bias_element, c0, sizeof(int32_t));
            }
            const size_t part_bytes = static_cast<size_t>(channels) * per_out;
            if (part_bytes <= lent.bytes) {
                const int remainder_at = static_cast<const char *>(part.filter_element_unaligned) -
                                         static_cast<const char *>(part.filter_element);
                memcpy(lent.mem, part.filter_element, part_bytes);
                part.filter_element = lent.mem;
                part.filter_element_unaligned = lent.mem + remainder_at;
            }
            for (int r = 0; r < h; r++) {
                build_row(smalls, others, r, w, c_small, c_other);
                dl::base::ConvArgsType band = part;
                band.input_element = s_row.mem;
                band.input_height = 1;
                band.output_element = ys + r * y_row + c0;
                band.output_height = 1;
                dl::base::conv2d(&band, quant_type, fn_[0], fn_[1], fn_[2]);
                for (int p = 0; p < (channels == cy ? 1 : w); p++) {
                    ai_engine_lut_s8_esp32s3(ys + r * y_row + c0 + p * cy,
                                             channels == cy ? static_cast<int>(y_row) : channels,
                                             turned_.data());
                }
            }
        }
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        int conv_exponent = 0;
        int up_first = 0;
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        fbs_model->get_operation_attribute(node_name, "up_first", up_first);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT ||
            fbs_model->get_operation_attribute(node_name, "conv_exponent", conv_exponent) != ESP_OK) {
            ESP_LOGE(TAG, "%s: not an int8 UpsampleConcatConv with its convolution exponent",
                     node_name.c_str());
            return nullptr;
        }
        return new UpsampleConcatConv(node_name.c_str(), conv_exponent, up_first != 0);
    }

  private:
    // Row r of the Concat: the doubled map's row r / 2, each pixel twice, and the other map's row r, in the
    // Concat's order.
    void build_row(const int8_t *smalls, const int8_t *others, int r, int w, int c_small,
                   int c_other) const noexcept
    {
        const int c = c_small + c_other;
        const int at_small = up_first_ ? 0 : c_other;
        const int at_other = up_first_ ? c_small : 0;
        const int8_t *up = smalls + static_cast<size_t>(r / 2) * (w / 2) * c_small;
        const int8_t *right = others + static_cast<size_t>(r) * w * c_other;
        for (int p = 0; p < w; p++) {
            memcpy(s_row.mem + p * c + at_small, up + (p / 2) * c_small, c_small);
            memcpy(s_row.mem + p * c + at_other, right + p * c_other, c_other);
        }
    }

    int conv_exponent_;
    bool up_first_;
    bool laid_ = false;
    std::array<int8_t, kTableBytes> turned_ = {};
    dl_kernel_erased_t fn_[3] = {};
};

} // namespace

dl::module::Module *upsample_concat_conv_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return UpsampleConcatConv::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
