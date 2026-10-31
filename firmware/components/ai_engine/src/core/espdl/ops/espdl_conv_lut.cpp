#include "core/espdl/ops/espdl_conv_lut.hpp"

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
#include "dl_base_depthwise_conv2d.hpp"
#include "dl_base_lut.hpp"
#include "dl_module_base.hpp"
#include "esp_log.h"
#include "fbs_model.hpp"

extern "C" {
void ai_engine_stem3_s8_esp32s3(int8_t *out, const int32_t *run);
void ai_engine_stem3_top_s8_esp32s3(int8_t *out, const int32_t *run);
}

namespace ai {

namespace {

const char *TAG = "conv_lut";

constexpr size_t kRank = 4;
constexpr size_t kPads = 4;
constexpr size_t kSpatial = 2;
constexpr int kBandBytes = CONFIG_ESP32S3_DATA_CACHE_SIZE / 4; // a band's output, as PointwiseS8's chunks
constexpr int kLoanFilterBytes = 32 * 1024;                    // the most weights a pass copies to the loan
constexpr int kVectorBytes = 16;
constexpr int kTableSpan = 65536; // int16 inputs an int16 table steps over
constexpr int kTableMiddle = 128; // an int8 table's entry for input 0
constexpr int kStemInputs = 3;    // channels a stem pixel holds
constexpr int kStemTaps = 3;      // a stem window's rows and columns
constexpr int kStemRowBytes = kStemTaps * kStemInputs * kVectorBytes; // a window row's weights

// Slots of ai_engine_stem3's run.
enum Stem {
    kStemPixels = 0,
    kStemStep,
    kStemRows,
    kStemWeights = kStemRows + kStemTaps,
    kStemShift,
    kStemBias,
    kStemSize
};

struct Rows {
    int start; // first input row a band reads
    int end;   // one past its last
};

bool aligned(const void *p) noexcept
{
    return reinterpret_cast<uintptr_t>(p) % kVectorBytes == 0;
}

int32_t word_of(const void *p) noexcept
{
    return static_cast<int32_t>(reinterpret_cast<uintptr_t>(p));
}

enum Input { kX = 0, kFilter, kBias };

// Inputs as esp_ppq_patches' conv_lut and conv_lut_plain write them: x [1, H, W, C], esp-dl's filter, the
// bias if any, and the table esp-dl appends for a node naming a "lut", whose grid the output then takes.
class ConvLut final : public dl::module::Module {
  public:
    ConvLut(const char *name, dl::quant_type_t quant, std::vector<int> pads, std::vector<int> strides,
            std::vector<int> dilations, int group, int conv_exponent, bool tabled)
        : Module(name, dl::MODULE_NON_INPLACE, quant), pads_(std::move(pads)), strides_(std::move(strides)),
          dilations_(std::move(dilations)), group_(group), conv_exponent_(conv_exponent), tabled_(tabled)
    {}

    ConvLut(const ConvLut &) = delete;
    ConvLut &operator=(const ConvLut &) = delete;

    bool is_lut_module() const override
    {
        return tabled_;
    }

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        const std::vector<int> &x = input_shapes[kX];
        const std::vector<int> &filter = input_shapes[kFilter];
        std::vector<int> y = x;
        if (x.size() != kRank || filter.size() != kRank) { return {y}; }
        y[1] = (x[1] + pads_[0] + pads_[1] - dilations_[0] * (filter[0] - 1) - 1) / strides_[0] + 1;
        y[2] = (x[2] + pads_[2] + pads_[3] - dilations_[1] * (filter[1] - 1) - 1) / strides_[1] + 1;
        y[3] = group_ == 1 ? filter[3] : x[3];
        return {y};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[kX]);
        dl::TensorBase *filter = context->get_tensor(m_inputs_index[kFilter]);
        dl::TensorBase *bias = biased() ? context->get_tensor(m_inputs_index[kBias]) : nullptr;
        dl::TensorBase *table = tabled_ ? context->get_tensor(m_inputs_index.back()) : nullptr;
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        if (bias != nullptr && !bias_laid_) {
            bias->reset_bias_layout(quant_type, group_ != 1);
            bias_laid_ = true;
        }
        // The kernels shift each sum to the convolution's own grid; the table takes it to y's.
        const int table_exponent = y->exponent;
        y->exponent = conv_exponent_;
        dl::base::ConvOpArgs args(y, x, pads_, filter, strides_, dilations_, group_, bias, dl::Linear,
                                  nullptr, dl::RUNTIME_MODE_SINGLE_CORE, quant_type);
        y->exponent = table_exponent;
        if (args.size() != 1 ||
            !dl::base::dl_conv_ensure_kernels(fn_, args.get_args(0), group_ != 1, quant_type, TAG)) {
            ESP_LOGE(TAG, "%s: esp-dl has no kernel for this convolution", name);
            return;
        }
        const dl::base::ConvArgsType &whole = args.get_args(0);
        if (!asked_) {
            loan_ask(std::min(filter->get_bytes(), kLoanFilterBytes));
            asked_ = true;
        }
        alignas(kVectorBytes) std::array<int8_t, kStemTaps * kStemRowBytes> left;
        const bool stem = stem_fits(whole);
        if (stem) { lay_left(whole, left.data()); }
        run_bands(whole, filter->get_bytes(), table != nullptr ? table->get_size() : 0,
                  table != nullptr ? table->get_element_ptr() : nullptr, stem ? left.data() : nullptr);
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        std::vector<int> pads;
        std::vector<int> strides;
        std::vector<int> dilations;
        int group = 1;
        int conv_exponent = 0;
        dl::quant_type_t quant = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "pads", pads);
        fbs_model->get_operation_attribute(node_name, "strides", strides);
        fbs_model->get_operation_attribute(node_name, "dilations", dilations);
        fbs_model->get_operation_attribute(node_name, "group", group);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant);
        const bool quantised = quant == dl::QUANT_TYPE_SYMM_8BIT || quant == dl::QUANT_TYPE_SYMM_16BIT;
        if (fbs_model->get_operation_attribute(node_name, "conv_exponent", conv_exponent) != ESP_OK ||
            !quantised || strides.size() != kSpatial || dilations.size() != kSpatial ||
            (!pads.empty() && pads.size() != kPads)) {
            ESP_LOGE(TAG, "%s: not an int8 or int16 2-d ConvLut", node_name.c_str());
            return nullptr;
        }
        // ONNX lists pads top, left, bottom, right; esp-dl's kernels take top, bottom, left, right.
        pads =
            pads.empty() ? std::vector<int>(kPads, 0) : std::vector<int>{pads[0], pads[2], pads[1], pads[3]};
        std::string lut;
        const bool tabled = fbs_model->get_operation_lut_name(node_name, lut) == ESP_OK;
        return new ConvLut(node_name.c_str(), quant, std::move(pads), std::move(strides),
                           std::move(dilations), group, conv_exponent, tabled);
    }

  private:
    bool biased() const noexcept
    {
        return m_inputs_index.size() == kBias + (tabled_ ? 2 : 1);
    }

    // Output channels of one pass: all of them when the loan holds the whole filter or nothing tiles, else
    // the most lane groups whose weights the loan holds.
    int channel_tile(const dl::base::ConvArgsType &whole, int filter_bytes, size_t lent,
                     int bytes) const noexcept
    {
        const int lanes = kVectorBytes / bytes;
        const int per_out = whole.filter_height * whole.filter_width * whole.filter_c * bytes;
        const int tile =
            static_cast<int>(std::min(lent, static_cast<size_t>(kLoanFilterBytes))) / per_out / lanes * lanes;
        // A negative shift selects per-channel factors, which esp-dl's own tiles leave whole on the S3.
        const bool whole_only = group_ != 1 || whole.mac_shift < 0 || whole.n_remainder != 0;
        return whole_only || static_cast<size_t>(filter_bytes) <= lent || tile < lanes ? whole.output_channel
                                                                                       : tile;
    }

    // Output rows of one band: its output lines a quarter of the cache.
    static int band_rows(const dl::base::ConvArgsType &whole, int bytes) noexcept
    {
        return std::max(1, kBandBytes / (whole.output_width * whole.output_channel * bytes));
    }

    static Rows input_rows(const dl::base::ConvArgsType &whole, int o0, int o1) noexcept
    {
        const int reach = whole.dilation_h * (whole.filter_height - 1) + 1;
        const int first = o0 * whole.stride_y - whole.padding_h_head;
        const int end =
            o1 == whole.output_height
                ? whole.input_height
                : std::min(whole.input_height, (o1 - 1) * whole.stride_y - whole.padding_h_head + reach);
        return {std::max(0, first), end};
    }

    // Rows [o0, o1) and channels [c0, c0 + tile) a pass, the head's padding in the first band only, the
    // tail's in the last; each pass reads its weights from the loan when it holds them (KEHOACH 6.5).
    void run_bands(const dl::base::ConvArgsType &whole, int filter_bytes, int entries, const void *table,
                   const int8_t *left) noexcept
    {
        const int bytes = dl::base::conv_feature_bytes(quant_type);
        const int in_row = whole.input_width * whole.input_channel;
        const int out_row = whole.output_width * whole.output_channel;
        const Loan lent = loan_lent();
        const int tile = channel_tile(whole, filter_bytes, lent.bytes, bytes);
        const int rows = band_rows(whole, bytes);
        const int step =
            quant_type == dl::QUANT_TYPE_SYMM_16BIT && entries > 1 ? kTableSpan / (entries - 1) : 1;
        for (int c0 = 0; c0 < whole.output_channel; c0 += tile) {
            dl::base::ConvArgsType part = tile_of(whole, c0, tile, bytes);
            const int part_bytes =
                tile == whole.output_channel
                    ? filter_bytes
                    : part.output_channel * whole.filter_height * whole.filter_width * whole.filter_c * bytes;
            if (static_cast<size_t>(part_bytes) <= lent.bytes) {
                const int remainder_at = static_cast<const char *>(part.filter_element_unaligned) -
                                         static_cast<const char *>(part.filter_element);
                memcpy(lent.mem, part.filter_element, part_bytes);
                part.filter_element = lent.mem;
                part.filter_element_unaligned = lent.mem + remainder_at;
            }
            for (int o0 = 0; o0 < whole.output_height; o0 += rows) {
                const int o1 = std::min(whole.output_height, o0 + rows);
                const Rows in = input_rows(whole, o0, o1);
                const bool more_rows = o1 < whole.output_height;
                dl::base::ConvArgsType band = part;
                band.padding_h_head = in.start - (o0 * whole.stride_y - whole.padding_h_head);
                band.padding_h_tail = more_rows ? 0 : whole.padding_h_tail;
                band.input_height = in.end - in.start;
                band.input_element = dl::base::conv_ptr_add(whole.input_element, in.start * in_row, bytes);
                band.output_height = o1 - o0;
                band.output_element = dl::base::conv_ptr_add(part.output_element, o0 * out_row, bytes);
                if (left != nullptr) {
                    stem_rows(part, o0, o1, left);
                } else if (group_ == 1) {
                    dl::base::conv2d(&band, quant_type, fn_[0], fn_[1], fn_[2]);
                } else {
                    dl::base::depthwise_conv2d(&band, quant_type, fn_[0], fn_[1], fn_[2]);
                }
                if (table == nullptr) { continue; }
                if (tile == whole.output_channel) {
                    look_up(band.output_element, band.output_height * out_row, table, step);
                } else {
                    look_up_tile(band.output_element, band.output_height * whole.output_width,
                                 part.output_channel, whole.output_channel, table, step);
                }
            }
        }
    }

    // An RGB stem ai_engine_stem3 runs: 3 int8 inputs into 16 outputs, 3x3 of stride 2 whose windows stay
    // within the map on its bottom and right, and rows 16 bytes apart a whole number of times.
    bool stem_fits(const dl::base::ConvArgsType &a) const noexcept
    {
        const bool shape = a.input_channel == kStemInputs && a.output_channel == kVectorBytes &&
                           a.filter_height == kStemTaps && a.filter_width == kStemTaps && a.stride_y == 2 &&
                           a.stride_x == 2 && a.dilation_h == 1 && a.dilation_w == 1 &&
                           a.padding_h_head == 1 && a.padding_w_head == 1;
        const bool inside = a.input_height % 2 == 0 && a.input_width % 2 == 0 &&
                            a.input_width * kStemInputs % kVectorBytes == 0;
        return quant_type == dl::QUANT_TYPE_SYMM_8BIT && group_ == 1 && shape && inside && a.mac_shift >= 1 &&
               a.bias_element != nullptr && aligned(a.output_element) && aligned(a.filter_element);
    }

    // The filter without its first column, the one the left edge's windows put on the padding.
    static void lay_left(const dl::base::ConvArgsType &a, int8_t *left) noexcept
    {
        memcpy(left, a.filter_element, kStemTaps * kStemRowBytes);
        for (int r = 0; r < kStemTaps; r++) {
            memset(left + r * kStemRowBytes, 0, kStemInputs * kVectorBytes);
        }
    }

    // Output rows [o0, o1) of the stem: the left edge on left, the rest of a row on the filter, and the row
    // whose first window row lies in the head's padding on the top kernel, past that row's weights.
    static void stem_rows(const dl::base::ConvArgsType &a, int o0, int o1, const int8_t *left) noexcept
    {
        const int8_t *xs = static_cast<const int8_t *>(a.input_element);
        const int in_row = a.input_width * kStemInputs;
        const std::array<const int8_t *, 2> weights = {left, static_cast<const int8_t *>(a.filter_element)};
        std::array<int32_t, kStemSize> run = {};
        run[kStemStep] = a.stride_x * kStemInputs;
        run[kStemShift] = a.mac_shift - 1;
        run[kStemBias] = word_of(a.bias_element);
        for (int oy = o0; oy < o1; oy++) {
            const int iy = oy * a.stride_y - a.padding_h_head;
            const bool top = iy < 0;
            int8_t *ys = static_cast<int8_t *>(a.output_element) + oy * a.output_y_offset;
            for (int ox = 0; ox < 2; ox++) {
                const int ix = ox * a.stride_x - a.padding_w_head;
                run[kStemPixels] = ox == 0 ? 1 : a.output_width - 1;
                for (int r = 0; r < kStemTaps; r++) {
                    const int row = std::min(a.input_height - 1, (top ? 0 : iy) + r);
                    run[kStemRows + r] = word_of(xs + row * in_row + ix * kStemInputs);
                }
                run[kStemWeights] = word_of(weights[ox] + (top ? kStemRowBytes : 0));
                int8_t *out = ys + ox * a.output_x_offset;
                if (top) {
                    ai_engine_stem3_top_s8_esp32s3(out, run.data());
                } else {
                    ai_engine_stem3_s8_esp32s3(out, run.data());
                }
            }
        }
    }

    // Channels [c0, c0 + tile) of whole as esp-dl's tiled loop lays a tile: each lane group's weights, bias
    // and output follow one another, so a tile only moves their starts.
    static dl::base::ConvArgsType tile_of(const dl::base::ConvArgsType &whole, int c0, int tile,
                                          int bytes) noexcept
    {
        dl::base::ConvArgsType part = whole;
        if (tile == whole.output_channel) { return part; }
        const int lanes = kVectorBytes / bytes;
        const int per_out = whole.filter_height * whole.filter_width * whole.filter_c;
        const int bias_bytes =
            bytes == 1 ? static_cast<int>(sizeof(int32_t)) : static_cast<int>(sizeof(int64_t));
        const int channels = std::min(tile, whole.output_channel - c0);
        part.n_div_x = channels / lanes;
        part.output_channel = channels;
        part.filter_element = dl::base::conv_ptr_add(whole.filter_element, c0 * per_out, bytes);
        if (whole.bias_element != nullptr) {
            part.bias_element = dl::base::conv_ptr_add(whole.bias_element, c0, bias_bytes);
        }
        part.output_element = dl::base::conv_ptr_add(whole.output_element, c0, bytes);
        return part;
    }

    void look_up(void *values, int n, const void *table, int step) const noexcept
    {
        if (quant_type == dl::QUANT_TYPE_SYMM_8BIT) {
            int8_t *v = static_cast<int8_t *>(values);
            dl::base::lut_s8(v, v, n, static_cast<const int8_t *>(table));
        } else {
            int16_t *v = static_cast<int16_t *>(values);
            dl::base::lut_s16_nearest_neighbor(v, v, n, static_cast<const int16_t *>(table), step);
        }
    }

    // n values a pixel, each pixel stride values after the last, through the table as look_up does.
    void look_up_tile(void *values, int pixels, int n, int stride, const void *table, int step) const noexcept
    {
        if (quant_type == dl::QUANT_TYPE_SYMM_8BIT) {
            const int8_t *middle = static_cast<const int8_t *>(table) + kTableMiddle;
            int8_t *v = static_cast<int8_t *>(values);
            for (int p = 0; p < pixels; p++, v += stride) {
                for (int i = 0; i < n; i++) {
                    v[i] = middle[v[i]];
                }
            }
            return;
        }
        int16_t *v = static_cast<int16_t *>(values);
        for (int p = 0; p < pixels; p++, v += stride) {
            dl::base::lut_s16_nearest_neighbor(v, v, n, static_cast<const int16_t *>(table), step);
        }
    }

    std::vector<int> pads_;
    std::vector<int> strides_;
    std::vector<int> dilations_;
    int group_;
    int conv_exponent_;
    bool tabled_;
    bool bias_laid_ = false;
    bool asked_ = false;
    dl_kernel_erased_t fn_[3] = {};
};

} // namespace

dl::module::Module *conv_lut_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return ConvLut::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
