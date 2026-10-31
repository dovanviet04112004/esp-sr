#include "core/espdl/ops/espdl_c3k2_block.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <string>
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

const char *TAG = "c3k2_block";

constexpr size_t kRank = 4;
constexpr int kConvs = 4;         // cv1, m1, m2, cv2
constexpr int kRing = 3;          // rows a 3x3 window reads
constexpr int kTableBytes = 256;  // an int8 table's entries
constexpr int kTableMiddle = 128; // its entry for input 0
constexpr size_t kAlignBytes = 16;

enum Conv { kCv1 = 0, kM1, kM2, kCv2 };

struct Scratch {
    int8_t *mem;
    size_t bytes;
};
// The middle rows of a C3k2Block run, shared by every block; grows only while graphs are built.
Scratch s_scratch = {nullptr, 0};

bool scratch_holds(size_t bytes) noexcept
{
    if (bytes <= s_scratch.bytes) { return true; }
    heap_caps_free(s_scratch.mem);
    s_scratch.mem = static_cast<int8_t *>(heap_caps_aligned_alloc(kAlignBytes, bytes, MALLOC_CAP_SPIRAM));
    s_scratch.bytes = s_scratch.mem != nullptr ? bytes : 0;
    return s_scratch.mem != nullptr;
}

size_t aligned_up(size_t bytes) noexcept
{
    return (bytes + kAlignBytes - 1) / kAlignBytes * kAlignBytes;
}

int input_of(int conv) noexcept
{
    return 1 + 2 * conv;
}

int8_t sum_s8(int8_t a, int8_t b) noexcept
{
    return static_cast<int8_t>(std::clamp(a + b, -128, 127));
}

// Inputs as esp_ppq_patches.c3k2_block writes them: x, then filter and bias of cv1 (1x1 into 2c), m1 (3x3
// into h), m2 (3x3 into c) and cv2 (1x1 from 3c); y, y1, z and the Concat share one exponent.
class C3k2Block final : public dl::module::Module {
  public:
    C3k2Block(const char *name, std::array<dl::TensorBase *, kConvs> tables,
              std::array<int, kConvs> exponents)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), tables_(tables),
          exponents_(exponents)
    {
        for (int k = 0; k < kConvs; k++) {
            const int8_t *table = tables_[k]->get_element_ptr<int8_t>();
            memcpy(turned_[k].data(), table + kTableMiddle, kTableBytes - kTableMiddle);
            memcpy(turned_[k].data() + kTableBytes - kTableMiddle, table, kTableMiddle);
        }
    }

    ~C3k2Block() override
    {
        for (dl::TensorBase *table : tables_) {
            delete table;
        }
    }

    C3k2Block(const C3k2Block &) = delete;
    C3k2Block &operator=(const C3k2Block &) = delete;

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        std::vector<int> y = input_shapes[0];
        const std::vector<int> &filter = input_shapes[input_of(kCv2)];
        if (y.size() == kRank && filter.size() == kRank) { y[3] = filter[3]; }
        return {y};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[0]);
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        std::array<dl::TensorBase *, kConvs> filters = {};
        std::array<dl::TensorBase *, kConvs> biases = {};
        size_t filter_bytes = 0;
        for (int k = 0; k < kConvs; k++) {
            filters[k] = context->get_tensor(m_inputs_index[input_of(k)]);
            biases[k] = context->get_tensor(m_inputs_index[input_of(k) + 1]);
            if (!laid_) { biases[k]->reset_bias_layout(quant_type, false); }
            filter_bytes += aligned_up(filters[k]->get_bytes());
        }
        laid_ = true;
        if (!asked_) {
            loan_ask(filter_bytes);
            asked_ = true;
        }
        Shape s = {x->shape[1],
                   x->shape[2],
                   filters[kCv1]->shape[3] / 2,
                   filters[kM1]->shape[3],
                   filters[kCv2]->shape[2],
                   filters[kCv2]->shape[3]};
        if (s.cat != 3 * s.c || filters[kM2]->shape[3] != s.c) {
            ESP_LOGE(TAG, "%s: not a C3k2 of one bottleneck: %d, %d channels", name, s.c, s.cat);
            return;
        }
        const size_t cat_row = static_cast<size_t>(s.w) * s.cat;
        const size_t y1_row = static_cast<size_t>(s.w) * s.c;
        const size_t t_row = static_cast<size_t>(s.w) * s.hidden;
        if (!scratch_holds(kRing * cat_row + 2 * kRing * y1_row + 2 * kRing * t_row + y1_row)) {
            ESP_LOGE(TAG, "%s: no PSRAM for the block's rows", name);
            return;
        }
        Rows rows = {s_scratch.mem, s_scratch.mem + kRing * cat_row, nullptr, nullptr, cat_row, y1_row,
                     t_row};
        rows.t = rows.y1 + 2 * kRing * y1_row;
        rows.u = rows.t + 2 * kRing * t_row;
        const int shared = tables_[kCv1]->exponent;
        dl::TensorBase cat_map({1, s.h, s.w, s.cat}, rows.cat, shared, dl::DATA_TYPE_INT8, false);
        dl::TensorBase y_conv({1, s.h, s.w, 2 * s.c}, rows.cat, exponents_[kCv1], dl::DATA_TYPE_INT8, false);
        dl::TensorBase y1_map({1, s.h, s.w, s.c}, rows.y1, shared, dl::DATA_TYPE_INT8, false);
        dl::TensorBase t_conv({1, s.h, s.w, s.hidden}, rows.t, exponents_[kM1], dl::DATA_TYPE_INT8, false);
        dl::TensorBase t_map({1, s.h, s.w, s.hidden}, rows.t, tables_[kM1]->exponent, dl::DATA_TYPE_INT8,
                             false);
        dl::TensorBase u_conv({1, s.h, s.w, s.c}, rows.u, exponents_[kM2], dl::DATA_TYPE_INT8, false);
        const int table_exponent = y->exponent;
        y->exponent = exponents_[kCv2];
        std::vector<int> one = {1, 1};
        std::vector<int> flat = {0, 0, 0, 0};
        std::vector<int> around = {1, 1, 1, 1};
        dl::base::ConvOpArgs a1(&y_conv, x, flat, filters[kCv1], one, one, 1, biases[kCv1], dl::Linear,
                                nullptr, dl::RUNTIME_MODE_SINGLE_CORE, quant_type);
        dl::base::ConvOpArgs a2(&t_conv, &y1_map, around, filters[kM1], one, one, 1, biases[kM1], dl::Linear,
                                nullptr, dl::RUNTIME_MODE_SINGLE_CORE, quant_type);
        dl::base::ConvOpArgs a3(&u_conv, &t_map, around, filters[kM2], one, one, 1, biases[kM2], dl::Linear,
                                nullptr, dl::RUNTIME_MODE_SINGLE_CORE, quant_type);
        dl::base::ConvOpArgs a4(y, &cat_map, flat, filters[kCv2], one, one, 1, biases[kCv2], dl::Linear,
                                nullptr, dl::RUNTIME_MODE_SINGLE_CORE, quant_type);
        y->exponent = table_exponent;
        std::array<dl::base::ConvArgsType, kConvs> args = {a1.get_args(0), a2.get_args(0), a3.get_args(0),
                                                           a4.get_args(0)};
        for (int k = 0; k < kConvs; k++) {
            if (!dl::base::dl_conv_ensure_kernels(fn_[k], args[k], false, quant_type, TAG)) {
                ESP_LOGE(TAG, "%s: esp-dl has no kernel for convolution %d", name, k);
                return;
            }
        }
        // cv1 writes y0 and y1 straight into the Concat's row, whose pixels hold 3c values.
        args[kCv1].output_x_offset = s.cat;
        args[kCv1].output_y_offset = s.w * s.cat;
        lend_weights(args, filters);
        run_rows(args, s, rows, x->get_element_ptr<int8_t>(), y->get_element_ptr<int8_t>());
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        std::array<dl::TensorBase *, kConvs> tables = {};
        std::array<int, kConvs> exponents = {};
        bool whole = quant_type == dl::QUANT_TYPE_SYMM_8BIT;
        for (int k = 0; k < kConvs && whole; k++) {
            std::string lut;
            const std::string n = std::to_string(k + 1);
            whole = fbs_model->get_operation_lut_name(node_name, lut, "lut" + n) == ESP_OK &&
                    fbs_model->get_operation_attribute(node_name, "exponent" + n, exponents[k]) == ESP_OK;
            tables[k] = whole ? fbs_model->get_parameter(lut) : nullptr;
            whole = whole && tables[k] != nullptr && tables[k]->get_size() == kTableBytes;
        }
        if (!whole) {
            for (dl::TensorBase *table : tables) {
                delete table;
            }
            ESP_LOGE(TAG, "%s: not an int8 C3k2Block with four tables and exponents", node_name.c_str());
            return nullptr;
        }
        return new C3k2Block(node_name.c_str(), tables, exponents);
    }

  private:
    struct Shape {
        int h;      // the map's rows
        int w;      // its columns
        int c;      // y0's, y1's and z's channels
        int hidden; // the bottleneck's
        int cat;    // the Concat's, 3c
        int out;    // y's
    };
    struct Rows {
        int8_t *cat; // kRing Concat rows: y0 and y1 from cv1, z from the Add
        int8_t *y1;  // y1 rows, each twice: kRing in turn lie together
        int8_t *t;   // m1's rows, each twice
        int8_t *u;   // m2's row
        size_t cat_bytes;
        size_t y1_bytes;
        size_t t_bytes;
    };

    // Every filter copied to the run's loan when it holds them all; else each convolution reads its own in
    // place.
    static void lend_weights(std::array<dl::base::ConvArgsType, kConvs> &args,
                             const std::array<dl::TensorBase *, kConvs> &filters) noexcept
    {
        const Loan lent = loan_lent();
        size_t at = 0;
        for (dl::TensorBase *filter : filters) {
            at += aligned_up(filter->get_bytes());
        }
        if (lent.bytes < at) { return; }
        at = 0;
        for (int k = 0; k < kConvs; k++) {
            const int remainder_at = static_cast<const char *>(args[k].filter_element_unaligned) -
                                     static_cast<const char *>(args[k].filter_element);
            memcpy(lent.mem + at, args[k].filter_element, filters[k]->get_bytes());
            args[k].filter_element = lent.mem + at;
            args[k].filter_element_unaligned = lent.mem + at + remainder_at;
            at += aligned_up(filters[k]->get_bytes());
        }
    }

    // Output row r of a 3x3 convolution of stride 1 padded by 1 from the kRing rows around r at rows, every
    // row twice.
    void run_window(const dl::base::ConvArgsType &whole, int conv, const int8_t *ring, size_t row_bytes,
                    int r, int h, int8_t *out) const noexcept
    {
        const int first = std::max(0, r - 1);
        const int last = std::min(h - 1, r + 1);
        dl::base::ConvArgsType band = whole;
        band.input_element = const_cast<int8_t *>(ring + (first % kRing) * row_bytes);
        band.input_height = last - first + 1;
        band.padding_h_head = r == 0 ? 1 : 0;
        band.padding_h_tail = r == h - 1 ? 1 : 0;
        band.output_element = out;
        band.output_height = 1;
        dl::base::conv2d(&band, quant_type, fn_[conv][0], fn_[conv][1], fn_[conv][2]);
    }

    void run_pointwise(const dl::base::ConvArgsType &whole, int conv, const int8_t *in,
                       int8_t *out) const noexcept
    {
        dl::base::ConvArgsType band = whole;
        band.input_element = const_cast<int8_t *>(in);
        band.input_height = 1;
        band.output_element = out;
        band.output_height = 1;
        dl::base::conv2d(&band, quant_type, fn_[conv][0], fn_[conv][1], fn_[conv][2]);
    }

    // Step j makes y's row j into the Concat's ring, m1's row j - 1 and the block's row j - 2 (KEHOACH 3.18).
    void run_rows(const std::array<dl::base::ConvArgsType, kConvs> &args, const Shape &s, const Rows &rows,
                  const int8_t *xs, int8_t *ys) const noexcept
    {
        const size_t x_row = static_cast<size_t>(args[kCv1].input_width) * args[kCv1].input_channel;
        const size_t y_row = static_cast<size_t>(s.w) * s.out;
        for (int j = 0; j < s.h + 2; j++) {
            if (j < s.h) {
                int8_t *cat = rows.cat + (j % kRing) * rows.cat_bytes;
                run_pointwise(args[kCv1], kCv1, xs + j * x_row, cat);
                int8_t *y1 = rows.y1 + (j % kRing) * rows.y1_bytes;
                for (int p = 0; p < s.w; p++) {
                    ai_engine_lut_s8_esp32s3(cat + p * s.cat, 2 * s.c, turned_[kCv1].data());
                    memcpy(y1 + p * s.c, cat + p * s.cat + s.c, s.c);
                }
                memcpy(y1 + kRing * rows.y1_bytes, y1, rows.y1_bytes);
            }
            if (j >= 1 && j <= s.h) {
                const int r = j - 1;
                int8_t *t = rows.t + (r % kRing) * rows.t_bytes;
                run_window(args[kM1], kM1, rows.y1, rows.y1_bytes, r, s.h, t);
                ai_engine_lut_s8_esp32s3(t, static_cast<int>(rows.t_bytes), turned_[kM1].data());
                memcpy(t + kRing * rows.t_bytes, t, rows.t_bytes);
            }
            if (j >= 2) {
                const int r = j - 2;
                run_window(args[kM2], kM2, rows.t, rows.t_bytes, r, s.h, rows.u);
                ai_engine_lut_s8_esp32s3(rows.u, static_cast<int>(rows.y1_bytes), turned_[kM2].data());
                int8_t *cat = rows.cat + (r % kRing) * rows.cat_bytes;
                const int8_t *y1 = rows.y1 + (r % kRing) * rows.y1_bytes;
                for (int p = 0; p < s.w; p++) {
                    int8_t *z = cat + p * s.cat + 2 * s.c;
                    for (int i = 0; i < s.c; i++) {
                        z[i] = sum_s8(y1[p * s.c + i], rows.u[p * s.c + i]);
                    }
                }
                int8_t *out = ys + r * y_row;
                run_pointwise(args[kCv2], kCv2, cat, out);
                ai_engine_lut_s8_esp32s3(out, static_cast<int>(y_row), turned_[kCv2].data());
            }
        }
    }

    std::array<dl::TensorBase *, kConvs> tables_;
    std::array<int, kConvs> exponents_;
    std::array<std::array<int8_t, kTableBytes>, kConvs> turned_ = {};
    dl_kernel_erased_t fn_[kConvs][3] = {};
    bool laid_ = false;
    bool asked_ = false;
};

} // namespace

dl::module::Module *c3k2_block_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return C3k2Block::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
