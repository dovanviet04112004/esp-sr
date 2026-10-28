#include "core/espdl/espdl_conv_block.hpp"

#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <string>
#include <utility>
#include <vector>

#include "core/espdl/espdl_loan.hpp"
#include "core/espdl/espdl_preload.hpp"
#include "core/espdl/espdl_split.hpp"
#include "dl_module_base.hpp"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "fbs_model.hpp"

extern "C" {
void ai_engine_dw3x3_row_s8_esp32s3(int8_t *out, const int8_t *const *rows, const int8_t *taps,
                                    const int32_t *run);
void ai_engine_pad_row_s8_esp32s3(int8_t *out, const int8_t *in, const int32_t *run);
void ai_engine_pw_norm_row_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                      const int32_t *run);
void ai_engine_pw_residual_row_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                          const int32_t *run);
void ai_engine_pw_norm_c24_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                      const int32_t *run);
void ai_engine_pw_norm_c48_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                      const int32_t *run);
void ai_engine_pw_residual_c24_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                          const int32_t *run);
void ai_engine_pw_residual_c48_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                          const int32_t *run);
void ai_engine_pw_residual_x_c24_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                            const int32_t *run);
void ai_engine_pw_residual_x_c48_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                            const int32_t *run);
void ai_engine_pw_norm_c96_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                      const int32_t *run);
void ai_engine_pw_residual_c96_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                          const int32_t *run);
void ai_engine_pw_residual_x_c96_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                            const int32_t *run);
void ai_engine_pw_norm_c144_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                       const int32_t *run);
void ai_engine_pw_residual_c144_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                           const int32_t *run);
void ai_engine_pw_residual_x_c144_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                             const int32_t *run);
void ai_engine_pw_norm_c192_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                       const int32_t *run);
void ai_engine_pw_residual_c192_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                           const int32_t *run);
void ai_engine_pw_residual_x_c192_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                             const int32_t *run);
void ai_engine_pw_conv_k12_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                      const int32_t *run);
void ai_engine_pw_conv_k24_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                      const int32_t *run);
void ai_engine_pw_conv_k48_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                      const int32_t *run);
void ai_engine_pw_conv_rows_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                       const int32_t *run);
void ai_engine_pw_conv_k24_n12_s8_esp32s3(int8_t *out, const int8_t *in, const int8_t *filter,
                                          const int32_t *run);
}

namespace ai {

namespace {

const char *TAG = "conv_block";

constexpr int kRank = 4;
constexpr int kChannelStep = 16;
constexpr int kHalfStep = 8;
constexpr size_t kVectorAlignBytes = 16;
constexpr int kTaps = 9;
constexpr int kShifts = 6;
constexpr int kRing = 3;
// Two rings of three padded rows, then the rows the first and second depthwise steps write.
constexpr int kRows = 2 * kRing + 2;

enum Input { kX = 0, kDw1, kPw1, kPw1Bias, kBn, kBnBias, kDw2, kPw2, kPw2Bias, kInputs };
// "pre" and "post": a joined 1x1 convolution's values a pixel, then its shift.
enum Joined { kJoinedValues = 0, kJoinedShift, kJoinedSize };
enum Shift { kShiftDw1 = 0, kShiftPw1, kShiftBn, kShiftDw2, kShiftPw2, kShiftResidual };
constexpr int kRunPixels = 0; // every row kernel's run starts with its pixels
// Slots of conv_row_s8_esp32s3.S's runs that hold addresses.
enum NormRun { kNormBias = 6, kNormWeights, kNormBnBias, kNormSize = 10 };
enum ResidualRun { kResidualBias = 6, kResidualX, kResidualSize = 10 };
// Slots of conv_row_s8_esp32s3.S's PointwiseS8 run.
enum ConvRun { kConvBias = 6, kConvSize = 11 };
// Values a pixel the joined convolutions' kernels read in front of the block and give behind it.
constexpr int kPreSmall = 12;
constexpr int kPreWide = 24;
constexpr int kPreHeld = 48;
constexpr int kPostOutputs = 12;
// Pixels the shared kernels take a call, each strip's depthwise rows then staying cached for its pointwise.
constexpr int kStripPixels = 4;
constexpr int kLanesMax = 2;
// Columns a lane's x reaches past its outputs on each side: one for each depthwise step.
constexpr int kHalo = 2;

using RowKernel = void (*)(int8_t *out, const int8_t *in, const int8_t *filter, const int32_t *run);

// Channel counts whose pointwise rows run unrolled, held: their inputs in registers, rows whole; residual_x
// takes an x shift of 1 or more.
struct Unrolled {
    int channels;
    bool held;
    RowKernel norm;
    RowKernel residual;
    RowKernel residual_x;
};

constexpr Unrolled kUnrolled[] = {
    {24, true, ai_engine_pw_norm_c24_s8_esp32s3, ai_engine_pw_residual_c24_s8_esp32s3,
     ai_engine_pw_residual_x_c24_s8_esp32s3},
    {48, true, ai_engine_pw_norm_c48_s8_esp32s3, ai_engine_pw_residual_c48_s8_esp32s3,
     ai_engine_pw_residual_x_c48_s8_esp32s3},
    {96, false, ai_engine_pw_norm_c96_s8_esp32s3, ai_engine_pw_residual_c96_s8_esp32s3,
     ai_engine_pw_residual_x_c96_s8_esp32s3},
    {144, false, ai_engine_pw_norm_c144_s8_esp32s3, ai_engine_pw_residual_c144_s8_esp32s3,
     ai_engine_pw_residual_x_c144_s8_esp32s3},
    {192, false, ai_engine_pw_norm_c192_s8_esp32s3, ai_engine_pw_residual_c192_s8_esp32s3,
     ai_engine_pw_residual_x_c192_s8_esp32s3},
};

int padded(int channels) noexcept
{
    return (channels + kChannelStep - 1) / kChannelStep * kChannelStep;
}

int32_t word_of(const void *p) noexcept
{
    return static_cast<int32_t>(reinterpret_cast<uintptr_t>(p));
}

// A weight's bytes in the loan, each kept 16-byte aligned for the vector loads.
size_t loaned_bytes(dl::TensorBase *tensor) noexcept
{
    return (static_cast<size_t>(tensor->get_bytes()) + kVectorAlignBytes - 1) / kVectorAlignBytes *
           kVectorAlignBytes;
}

// Inputs as esp_ppq_patches.conv_block_from lays them: x [1, T, F, C], taps [9][Cp], pointwise
// [Cp / 16][C][16], bias and norm weights [Cp], then any "pre", "post" ones; with "pre", x is its rows.
class ConvBlock final : public dl::module::Module {
  public:
    ConvBlock(const char *name, std::vector<int> shifts, std::vector<int> pre, std::vector<int> post,
              std::vector<int> out_shape)
        : Module(name, dl::MODULE_NON_INPLACE, dl::QUANT_TYPE_SYMM_8BIT), shifts_(std::move(shifts)),
          pre_(std::move(pre)), post_(std::move(post)), out_shape_(std::move(out_shape))
    {}

    ~ConvBlock() override
    {
        heap_caps_free(whole_.rows);
    }

    ConvBlock(const ConvBlock &) = delete;
    ConvBlock &operator=(const ConvBlock &) = delete;

    std::vector<std::vector<int>> get_output_shape(std::vector<std::vector<int>> &input_shapes) override
    {
        if (!out_shape_.empty()) { return {out_shape_}; }
        return {input_shapes[kX]};
    }

    void forward(dl::ModelContext *context, dl::runtime_mode_t) override
    {
        dl::TensorBase *x = context->get_tensor(m_inputs_index[kX]);
        dl::TensorBase *y = context->get_tensor(m_outputs_index[0]);
        if (!built_ && !build(context, x, y)) { return; }
        const Loan lent = loan_lent();
        int8_t *loan = loan_bytes_ > 0 && lent.bytes >= loan_bytes_ ? lent.mem : nullptr;
        const size_t room = loan != nullptr && rings_bytes_ > 0 ? lent.bytes - loan_bytes_ : 0;
        // Halves run only in the loan: PSRAM a part wrote and read back, the other core running, came back
        // wrong.
        const int half_strip = halved_ ? split_strip(room) : 0;
        split_ = half_strip > 0;
        const bool rings = !split_ && room >= rings_bytes_ + kStripPixels * padded_;
        strip_ = split_  ? half_strip
                 : held_ ? width_
                 : rings ? widest_strip(room - rings_bytes_)
                         : kStripPixels;
        const size_t before_ends = loan_bytes_ + (split_  ? halves_bytes(strip_)
                                                  : rings ? rings_bytes_ + strip_ * padded_
                                                          : 0);
        int8_t *ends =
            loan != nullptr && lent.bytes >= before_ends + ends_bytes_ ? lent.mem + before_ends : nullptr;
        dw1_ = weights(context, kDw1, loan);
        pw1_ = weights(context, kPw1, loan);
        dw2_ = weights(context, kDw2, loan);
        pw2_ = weights(context, kPw2, loan);
        const int32_t pw1_bias = word_of(weights(context, kPw1Bias, loan));
        const int32_t bn = word_of(weights(context, kBn, loan));
        const int32_t bn_bias = word_of(weights(context, kBnBias, loan));
        const int32_t pw2_bias = word_of(weights(context, kPw2Bias, loan));
        if (split_) {
            place_halves(loan);
        } else {
            place_whole(rings ? loan : nullptr);
        }
        int32_t pre_bias = 0;
        int32_t post_bias = 0;
        if (!pre_.empty()) {
            pre_weights_ = weights(context, kInputs, ends);
            pre_bias = word_of(weights(context, kInputs + 1, ends));
        }
        if (!post_.empty()) {
            const int at = kInputs + (pre_.empty() ? 0 : 2);
            post_weights_ = weights(context, at, ends);
            post_bias = word_of(weights(context, at + 1, ends));
        }
        for (Lane *lane : {&whole_, &halves_[0], &halves_[1]}) {
            lane->runs.norm[kNormBias] = pw1_bias;
            lane->runs.norm[kNormWeights] = bn;
            lane->runs.norm[kNormBnBias] = bn_bias;
            lane->runs.residual[kResidualBias] = pw2_bias;
            lane->runs.pre[kConvBias] = pre_bias;
            lane->runs.post[kConvBias] = post_bias;
        }
        xs_ = x->get_element_ptr<int8_t>();
        ys_ = y->get_element_ptr<int8_t>();
        if (split_) {
            split_run(run_part, this);
        } else {
            run_lane(whole_);
        }
    }

    void print() override
    {
        ESP_LOGI(TAG, "shifts: %s, quant_type: %s.", dl::vector_to_string(shifts_).c_str(),
                 dl::quant_type_to_string(quant_type));
    }

    static dl::module::Module *deserialize(fbs::FbsModel *fbs_model, std::string node_name)
    {
        std::vector<int> shifts;
        std::vector<int> pre;
        std::vector<int> post;
        std::vector<int> out_shape;
        dl::quant_type_t quant_type = dl::QUANT_TYPE_NONE;
        fbs_model->get_operation_attribute(node_name, "shifts", shifts);
        fbs_model->get_operation_attribute(node_name, "pre", pre);
        fbs_model->get_operation_attribute(node_name, "post", post);
        fbs_model->get_operation_attribute(node_name, "out_shape", out_shape);
        fbs_model->get_operation_attribute(node_name, "quant_type", quant_type);
        const bool joined_fit = (pre.empty() || pre.size() == kJoinedSize) &&
                                (post.empty() || post.size() == kJoinedSize) &&
                                (pre.empty() && post.empty() ? out_shape.empty() : out_shape.size() == kRank);
        if (quant_type != dl::QUANT_TYPE_SYMM_8BIT || shifts.size() != kShifts || !joined_fit) {
            ESP_LOGE(TAG, "%s: only an int8 block with six shifts runs as ConvBlock", node_name.c_str());
            return nullptr;
        }
        return new ConvBlock(node_name.c_str(), std::move(shifts), std::move(pre), std::move(post),
                             std::move(out_shape));
    }

  private:
    // A lane's kernel runs: run_lane works on a copy on the stack of the core running it, in internal RAM.
    struct Runs {
        std::array<int32_t, 3> dw1 = {};
        std::array<int32_t, 3> dw2 = {};
        std::array<int32_t, 3> pad = {};
        std::array<int32_t, kNormSize> norm = {};
        std::array<int32_t, kResidualSize> residual = {};
        std::array<int32_t, kConvSize> pre = {};
        std::array<int32_t, kConvSize> post = {};
    };

    // A lane's columns and rows: the whole row, or a frequency half on each core. A ring row holds the
    // columns from first, each padded_ bytes; columns outside the map stay zero.
    struct Lane {
        int lo = 0; // output columns [lo, hi)
        int hi = 0;
        int x_lo = 0; // x columns loaded, [x_lo, x_hi)
        int x_hi = 0;
        int c_lo = 0; // norm columns computed, [c_lo, c_hi)
        int c_hi = 0;
        int first = 0;
        int row_bytes = 0;
        int8_t *rows = nullptr; // PSRAM rows, for when the loan holds none
        int8_t *x_ring = nullptr;
        int8_t *c_ring = nullptr;
        int8_t *a = nullptr;
        int8_t *d = nullptr;
        int8_t *out_row = nullptr; // the block's output strip the post convolution reads
        Runs runs;
    };

    static void run_part(void *self, int part)
    {
        auto *block = static_cast<ConvBlock *>(self);
        block->run_lane(block->halves_[part]);
    }

    void run_lane(const Lane &lane) noexcept
    {
        Runs runs = lane.runs;
        Preload preload(split_);
        memset(lane.x_ring, 0, 2 * kRing * lane.row_bytes);
        load_x(lane, runs, 0);
        load_x(lane, runs, 1);
        norm_row(lane, runs, preload, 0);
        for (int t = 0; t < frames_; t++) {
            load_x(lane, runs, t + 2);
            preload_rows(lane, preload, t);
            norm_row(lane, runs, preload, t + 1);
            output_row(lane, runs, preload, t);
            preload.finish();
        }
    }

    int8_t *x_row(const Lane &lane, int t) const noexcept
    {
        return lane.x_ring + ((t + kRing) % kRing) * lane.row_bytes;
    }

    int8_t *c_row(const Lane &lane, int t) const noexcept
    {
        return lane.c_ring + ((t + 1) % kRing) * lane.row_bytes;
    }

    int8_t *column(const Lane &lane, int8_t *row, int f) const noexcept
    {
        return row + (f - lane.first) * padded_;
    }

    // The widest strip of whole pixels room holds, kStripPixels up to a row, leaving the joined weights
    // theirs when it can.
    int widest_strip(size_t room) const noexcept
    {
        const size_t beside_ends = room >= ends_bytes_ ? (room - ends_bytes_) / padded_ : 0;
        const size_t pixels = beside_ends >= kStripPixels ? beside_ends : room / padded_;
        return static_cast<int>(std::min<size_t>(pixels, width_));
    }

    size_t out_strip_bytes(int strip) const noexcept
    {
        const size_t bytes = post_.empty() ? 0 : static_cast<size_t>(strip) * channels_;
        return (bytes + kVectorAlignBytes - 1) / kVectorAlignBytes * kVectorAlignBytes;
    }

    // Both halves' rings, strip buffers and post convolution input strips.
    size_t halves_bytes(int strip) const noexcept
    {
        return halves_rings_bytes_ + kLanesMax * (strip * padded_ + out_strip_bytes(strip));
    }

    // The widest strip, kStripPixels up to a half's norm columns, at which room holds halves_bytes, leaving
    // the joined weights theirs when it can; 0 when no strip of kStripPixels fits.
    int split_strip(size_t room) const noexcept
    {
        const size_t pixel_bytes = kLanesMax * (padded_ + (post_.empty() ? 0 : channels_));
        const size_t fixed = halves_rings_bytes_ + kLanesMax * kVectorAlignBytes;
        if (room < fixed + kStripPixels * pixel_bytes) { return 0; }
        const size_t free = room - fixed;
        const size_t beside_ends = free >= ends_bytes_ ? (free - ends_bytes_) / pixel_bytes : 0;
        const size_t pixels = beside_ends >= kStripPixels ? beside_ends : free / pixel_bytes;
        return static_cast<int>(std::min<size_t>(pixels, halves_span_));
    }

    // The whole row's rings and one strip buffer at mem, else in its rows; the post convolution's input row
    // stays in the rows, since the joined weights may follow the rings in the loan.
    void place_whole(int8_t *mem) noexcept
    {
        whole_.x_ring = mem != nullptr ? mem : whole_.rows;
        whole_.c_ring = whole_.x_ring + kRing * whole_.row_bytes;
        whole_.a = whole_.c_ring + kRing * whole_.row_bytes;
        whole_.d = mem != nullptr ? whole_.a : whole_.a + whole_.row_bytes;
        whole_.out_row = whole_.rows + kRows * whole_.row_bytes;
    }

    // Both halves' rings, then each one's strip buffer and post convolution input strip, at mem in the loan.
    void place_halves(int8_t *mem) noexcept
    {
        int8_t *at = mem;
        for (Lane &half : halves_) {
            half.x_ring = at;
            half.c_ring = at + kRing * half.row_bytes;
            at += 2 * kRing * half.row_bytes;
        }
        for (Lane &half : halves_) {
            half.a = at;
            half.d = at;
            at += strip_ * padded_;
            half.out_row = at;
            at += out_strip_bytes(strip_);
        }
    }

    // Input's data, copied first to the loan's next free bytes when the block runs on a loan.
    const int8_t *weights(dl::ModelContext *context, int input, int8_t *&loan) const noexcept
    {
        dl::TensorBase *tensor = context->get_tensor(m_inputs_index[input]);
        const int8_t *data = tensor->get_element_ptr<int8_t>();
        if (loan == nullptr) { return data; }
        memcpy(loan, data, tensor->get_bytes());
        const int8_t *copy = loan;
        loan += loaned_bytes(tensor);
        return copy;
    }

    // Row t of x over the lane's columns into its ring slot; zeros past the frames.
    void load_x(const Lane &lane, Runs &runs, int t) noexcept
    {
        int8_t *slot = column(lane, x_row(lane, t), lane.x_lo);
        const int8_t *from =
            xs_ + (t * width_ + lane.x_lo) * (pre_.empty() ? channels_ : pre_[kJoinedValues]);
        if (t >= frames_) {
            memset(slot, 0, (lane.x_hi - lane.x_lo) * padded_);
        } else if (!pre_.empty()) {
            pre_kernel_(slot, from, pre_weights_, runs.pre.data());
        } else if (channels_ == padded_) {
            memcpy(slot, from, (lane.x_hi - lane.x_lo) * channels_);
        } else {
            ai_engine_pad_row_s8_esp32s3(slot, from, runs.pad.data());
        }
    }

    // The lane's bytes of output row t, then those load_x reads for row t + 3, into the dcache while the row
    // computes.
    void preload_rows(const Lane &lane, Preload &preload, int t) noexcept
    {
        const int out_values = post_.empty() ? channels_ : post_[kJoinedValues];
        const int in_values = pre_.empty() ? channels_ : pre_[kJoinedValues];
        const int8_t *y_row = ys_ + (t * width_ + lane.lo) * out_values;
        const size_t y_bytes = static_cast<size_t>(lane.hi - lane.lo) * out_values;
        if (split_) {
            preload.add_written(y_row, y_bytes);
        } else {
            preload.add(y_row, y_bytes);
        }
        if (t + 3 < frames_) {
            preload.add(xs_ + ((t + 3) * width_ + lane.x_lo) * in_values,
                        (lane.x_hi - lane.x_lo) * in_values);
        }
        preload.pump();
    }

    // The norm's output at row t over the lane's norm columns, from the first depthwise and pointwise; zeros
    // past the frames, as padding is.
    void norm_row(const Lane &lane, Runs &runs, Preload &preload, int t) noexcept
    {
        int8_t *row = c_row(lane, t);
        if (t >= frames_) {
            memset(column(lane, row, lane.c_lo), 0, (lane.c_hi - lane.c_lo) * padded_);
            return;
        }
        for (int f0 = lane.c_lo; f0 < lane.c_hi; f0 += strip_) {
            const int8_t *window[kRing] = {column(lane, x_row(lane, t - 1), f0 - 1),
                                           column(lane, x_row(lane, t), f0 - 1),
                                           column(lane, x_row(lane, t + 1), f0 - 1)};
            runs.dw1[kRunPixels] = std::min(strip_, lane.c_hi - f0);
            runs.norm[kRunPixels] = runs.dw1[kRunPixels];
            ai_engine_dw3x3_row_s8_esp32s3(lane.a, window, dw1_, runs.dw1.data());
            norm_kernel_(column(lane, row, f0), lane.a, pw1_, runs.norm.data());
            preload.pump();
        }
    }

    // Output row t over the lane's columns from the norm rows around it, plus x row t.
    void output_row(const Lane &lane, Runs &runs, Preload &preload, int t) noexcept
    {
        preload.settle_writes();
        for (int f0 = lane.lo; f0 < lane.hi; f0 += strip_) {
            const int8_t *window[kRing] = {column(lane, c_row(lane, t - 1), f0 - 1),
                                           column(lane, c_row(lane, t), f0 - 1),
                                           column(lane, c_row(lane, t + 1), f0 - 1)};
            runs.dw2[kRunPixels] = std::min(strip_, lane.hi - f0);
            runs.residual[kRunPixels] = runs.dw2[kRunPixels];
            ai_engine_dw3x3_row_s8_esp32s3(lane.d, window, dw2_, runs.dw2.data());
            runs.residual[kResidualX] = word_of(column(lane, x_row(lane, t), f0));
            if (post_.empty()) {
                residual_kernel_(ys_ + (t * width_ + f0) * channels_, lane.d, pw2_, runs.residual.data());
            } else {
                residual_kernel_(lane.out_row, lane.d, pw2_, runs.residual.data());
                runs.post[kRunPixels] = runs.dw2[kRunPixels];
                post_kernel_(ys_ + (t * width_ + f0) * post_[kJoinedValues], lane.out_row, post_weights_,
                             runs.post.data());
            }
            preload.pump();
        }
    }

    static bool aligned(const void *p) noexcept
    {
        return reinterpret_cast<uintptr_t>(p) % kVectorAlignBytes == 0;
    }

    // The block's channels, from its pointwise weights [Cp / 16][C][16] and taps [9][Cp].
    int block_channels(dl::ModelContext *context) const noexcept
    {
        const int cp = context->get_tensor(m_inputs_index[kDw1])->get_size() / kTaps;
        return cp > 0 ? context->get_tensor(m_inputs_index[kPw1])->get_size() / cp : 0;
    }

    bool shapes_fit(dl::ModelContext *context, dl::TensorBase *x, dl::TensorBase *y) const
    {
        const size_t inputs = kInputs + (pre_.empty() ? 0 : 2) + (post_.empty() ? 0 : 2);
        const int c = block_channels(context);
        const int cp = padded(c);
        if (m_inputs_index.size() != inputs || x->shape.size() != kRank ||
            x->get_dtype() != dl::DATA_TYPE_INT8 || y->get_dtype() != dl::DATA_TYPE_INT8 || c <= 0 ||
            c % kHalfStep != 0) {
            ESP_LOGE(TAG, "%s: %u inputs, or x and y not int8 maps of a block of C a multiple of 8", name,
                     static_cast<unsigned>(m_inputs_index.size()));
            return false;
        }
        const int k = pre_.empty() ? c : pre_[kJoinedValues];
        const int row = x->shape[2] * x->shape[3];
        const int n = post_.empty() ? c : post_[kJoinedValues];
        const bool pre_fits = k == kPreSmall || k == kPreWide || k == kPreHeld || k % kChannelStep == 0;
        const bool post_fits =
            (n == kPostOutputs && c == kPreWide) || (c % kChannelStep == 0 && n % kHalfStep == 0);
        const bool joined = (pre_.empty() || (pre_fits && row % k == 0)) && (post_.empty() || post_fits) &&
                            (!pre_.empty() || x->shape[3] == c);
        if (!joined || y->get_size() != x->shape[1] * (row / k) * n) {
            ESP_LOGE(TAG, "%s: the joined convolutions do not fit a block of %d channels", name, c);
            return false;
        }
        std::vector<int> sizes = {kTaps * cp, cp * c, cp, cp, cp, kTaps * cp, cp * c, cp};
        if (!pre_.empty()) { sizes.insert(sizes.end(), {cp * k, cp}); }
        if (!post_.empty()) { sizes.insert(sizes.end(), {padded(n) * c, padded(n)}); }
        for (size_t i = 0; i < sizes.size(); i++) {
            dl::TensorBase *p = context->get_tensor(m_inputs_index[kDw1 + i]);
            if (p->get_size() != sizes[i] || !aligned(p->get_element_ptr())) {
                ESP_LOGE(TAG, "%s: input %u holds %d values, the block wants %d", name,
                         static_cast<unsigned>(kDw1 + i), p->get_size(), sizes[i]);
                return false;
            }
        }
        for (int i = kShiftDw1; i <= kShiftResidual; i++) {
            if (shifts_[i] < (i == kShiftResidual ? 0 : 1)) {
                ESP_LOGE(TAG, "%s: shift %d is %d, the kernels round right shifts of 1 or more", name, i,
                         shifts_[i]);
                return false;
            }
        }
        if ((!pre_.empty() && pre_[kJoinedShift] < 1) || (!post_.empty() && post_[kJoinedShift] < 1)) {
            ESP_LOGE(TAG, "%s: a joined convolution's shift is under 1", name);
            return false;
        }
        return true;
    }

    void set_columns(Lane &lane, int lo, int hi) const noexcept
    {
        lane.lo = lo;
        lane.hi = hi;
        lane.x_lo = std::max(lo - kHalo, 0);
        lane.x_hi = std::min(hi + kHalo, width_);
        lane.c_lo = std::max(lo - 1, 0);
        lane.c_hi = std::min(hi + 1, width_);
        lane.first = std::max(lo - kHalo, -1);
        lane.row_bytes = (std::min(hi + kHalo, width_ + 1) - lane.first) * padded_;
    }

    // The whole row's lane with its PSRAM rows; and with two parts, a row wide enough and the shared kernels,
    // a frequency half's lane each, run only in the loan.
    bool place_lanes() noexcept
    {
        set_columns(whole_, 0, width_);
        const int out_bytes =
            post_.empty() ? 0 : (width_ * channels_ + kChannelStep - 1) / kChannelStep * kChannelStep;
        whole_.rows = static_cast<int8_t *>(heap_caps_aligned_calloc(
            kVectorAlignBytes, 1, kRows * whole_.row_bytes + out_bytes, MALLOC_CAP_SPIRAM));
        if (whole_.rows == nullptr) {
            ESP_LOGE(TAG, "%s: no memory for %d rows of %d bytes", name, kRows, whole_.row_bytes);
            return false;
        }
        halved_ = !held_ && split_parts() >= kLanesMax && width_ >= kLanesMax * kHalo;
        for (int l = 0; halved_ && l < kLanesMax; l++) {
            Lane &half = halves_[l];
            set_columns(half, width_ * l / kLanesMax, width_ * (l + 1) / kLanesMax);
            halves_rings_bytes_ += 2 * kRing * half.row_bytes;
            halves_span_ = std::max(halves_span_, half.c_hi - half.c_lo);
        }
        return true;
    }

    void set_runs(Lane &lane) const noexcept
    {
        const int vectors = padded_ / kChannelStep;
        const int whole = channels_ / kChannelStep;
        const int half = channels_ % kChannelStep != 0;
        // Whole input vectors whose last product loads the next; the tail then sums the last 16, or the 8
        // left.
        const int ahead = half ? whole : whole - 1;
        const int x_pixels = lane.x_hi - lane.x_lo;
        lane.runs.dw1 = {x_pixels, vectors, shifts_[kShiftDw1]};
        lane.runs.dw2 = {x_pixels, vectors, shifts_[kShiftDw2]};
        lane.runs.pad = {x_pixels, whole, half};
        lane.runs.norm = {x_pixels,           vectors, vectors, ahead, half,
                          shifts_[kShiftPw1], 0,       0,       0,     shifts_[kShiftBn]};
        lane.runs.residual = {x_pixels,
                              vectors,
                              vectors,
                              ahead,
                              half,
                              shifts_[kShiftPw2],
                              0,
                              0,
                              shifts_[kShiftResidual],
                              half ? kHalfStep : kChannelStep};
        if (!pre_.empty()) {
            const int in = pre_[kJoinedValues] / kChannelStep;
            lane.runs.pre = {x_pixels, vectors, in, in - 1, 0, pre_[kJoinedShift], 0, 0, -1, kChannelStep, 0};
        }
        if (!post_.empty() && post_kernel_ == ai_engine_pw_conv_k24_n12_s8_esp32s3) {
            lane.runs.post = {x_pixels, 1, 0, 0, 0, post_[kJoinedShift], 0, 0, -1, kChannelStep, 0};
        } else if (!post_.empty()) {
            const int n = post_[kJoinedValues];
            const int in = channels_ / kChannelStep;
            lane.runs.post = {x_pixels, padded(n) / kChannelStep,
                              in,       in - 1,
                              0,        post_[kJoinedShift],
                              0,        0,
                              -1,       n % kChannelStep != 0 ? kHalfStep : kChannelStep,
                              0};
        }
    }

    bool build(dl::ModelContext *context, dl::TensorBase *x, dl::TensorBase *y)
    {
        if (!shapes_fit(context, x, y)) { return false; }
        frames_ = x->shape[1];
        channels_ = block_channels(context);
        width_ = pre_.empty() ? x->shape[2] : x->shape[2] * x->shape[3] / pre_[kJoinedValues];
        padded_ = padded(channels_);
        std::vector<int> biases = {kPw1Bias, kBnBias, kPw2Bias};
        if (!pre_.empty()) { biases.push_back(kInputs + 1); }
        if (!post_.empty()) { biases.push_back(kInputs + (pre_.empty() ? 1 : 3)); }
        for (int i : biases) {
            context->get_tensor(m_inputs_index[i])->reset_bias_layout(quant_type, true);
        }
        for (const Unrolled &u : kUnrolled) {
            if (u.channels == channels_) {
                norm_kernel_ = u.norm;
                residual_kernel_ = shifts_[kShiftResidual] > 0 ? u.residual_x : u.residual;
                held_ = u.held;
            }
        }
        if (!pre_.empty()) {
            const int k = pre_[kJoinedValues];
            pre_kernel_ = k == kPreSmall  ? ai_engine_pw_conv_k12_s8_esp32s3
                          : k == kPreWide ? ai_engine_pw_conv_k24_s8_esp32s3
                          : k == kPreHeld ? ai_engine_pw_conv_k48_s8_esp32s3
                                          : ai_engine_pw_conv_rows_s8_esp32s3;
        }
        if (!post_.empty()) {
            const bool narrow = channels_ == kPreWide && post_[kJoinedValues] == kPostOutputs;
            post_kernel_ = narrow ? ai_engine_pw_conv_k24_n12_s8_esp32s3 : ai_engine_pw_conv_rows_s8_esp32s3;
        }
        if (!place_lanes()) { return false; }
        for (Lane *lane : {&whole_, &halves_[0], &halves_[1]}) {
            set_runs(*lane);
        }
        const bool shared = !held_;
        rings_bytes_ = shared ? 2 * kRing * whole_.row_bytes : 0;
        if (shared) {
            for (size_t i = kDw1; i < m_inputs_index.size(); i++) {
                (i < kInputs ? loan_bytes_ : ends_bytes_) +=
                    loaned_bytes(context->get_tensor(m_inputs_index[i]));
            }
            loan_ask(loan_bytes_);
        }
        built_ = true;
        return true;
    }

    std::vector<int> shifts_;
    std::vector<int> pre_;
    std::vector<int> post_;
    std::vector<int> out_shape_;
    int frames_ = 0;
    int width_ = 0;
    int channels_ = 0;
    int padded_ = 0;
    Lane whole_;
    std::array<Lane, kLanesMax> halves_ = {};
    bool halved_ = false; // halves_ laid out, for two parts
    bool split_ = false;  // this run on the halves
    int halves_span_ = 0; // the widest half's norm columns
    size_t halves_rings_bytes_ = 0;
    const int8_t *xs_ = nullptr;
    int8_t *ys_ = nullptr;
    const int8_t *dw1_ = nullptr;
    const int8_t *pw1_ = nullptr;
    const int8_t *dw2_ = nullptr;
    const int8_t *pw2_ = nullptr;
    const int8_t *pre_weights_ = nullptr;
    const int8_t *post_weights_ = nullptr;
    RowKernel norm_kernel_ = ai_engine_pw_norm_row_s8_esp32s3;
    RowKernel residual_kernel_ = ai_engine_pw_residual_row_s8_esp32s3;
    RowKernel pre_kernel_ = nullptr;
    RowKernel post_kernel_ = nullptr;
    bool held_ = false; // inputs held in registers, rows whole: no strips, no loan
    int strip_ = 0;
    size_t loan_bytes_ = 0;  // its weights' bytes; 0 for the unrolled kernels
    size_t rings_bytes_ = 0; // the whole row's two rings, when the loan may hold them
    size_t ends_bytes_ = 0;  // the joined convolutions' weights, after the rings
    bool built_ = false;
};

} // namespace

dl::module::Module *conv_block_from(fbs::FbsModel *fbs_model, std::string node_name)
{
    return ConvBlock::deserialize(fbs_model, std::move(node_name));
}

} // namespace ai
