// The cases of kernel_cases.h, included once a translation unit has bound espdl_kernels.hpp's names and defined
// KERNEL_CASES as the function to define (ADR-0045).

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace {

constexpr int kCases = 8;
constexpr size_t kVector = 16;
constexpr size_t kBiasBlock = 64; // 16 channels' bias in reset_bias_layout's order

// xorshift32: the same numbers on the chip and on the host.
struct Rng {
    uint32_t state;

    uint32_t next()
    {
        state ^= state << 13;
        state ^= state >> 17;
        state ^= state << 5;
        return state;
    }

    int below(int n) { return static_cast<int>(next() % static_cast<uint32_t>(n)); }

    void fill(void *buffer, size_t n)
    {
        auto *bytes = static_cast<uint8_t *>(buffer);
        for (size_t i = 0; i < n; i++) {
            bytes[i] = static_cast<uint8_t>(next() >> 24);
        }
    }
};

Rng rng_for(const char *kernel, int index)
{
    uint32_t h = 2166136261u;
    for (const char *c = kernel; *c != '\0'; c++) {
        h = (h ^ static_cast<uint8_t>(*c)) * 16777619u;
    }
    h ^= static_cast<uint32_t>(index + 1) * 0x9e3779b9u;
    return Rng{h != 0 ? h : 1};
}

int32_t slot(const void *p)
{
    return static_cast<int32_t>(reinterpret_cast<intptr_t>(p));
}

int32_t bits(float f)
{
    int32_t b;
    memcpy(&b, &f, sizeof(b));
    return b;
}

// The case's buffers, filled with its numbers, all released together.
class Case {
  public:
    Case(const KernelSink *sink, const char *kernel, int index)
        : sink_(sink), kernel_(kernel), index_(index), rng_(rng_for(kernel, index))
    {}

    ~Case()
    {
        for (int i = 0; i < count_; i++) {
            sink_->release(buffers_[i]);
        }
    }

    template <typename T> T *random(size_t n)
    {
        T *b = zeros<T>(n);
        rng_.fill(b, n * sizeof(T));
        return b;
    }

    template <typename T> T *zeros(size_t n)
    {
        const size_t bytes = (n * sizeof(T) + 2 * kVector - 1) / kVector * kVector + kVector;
        void *b = sink_->alloc(bytes);
        memset(b, 0, bytes);
        buffers_[count_++] = b;
        return static_cast<T *>(b);
    }

    // Floats in [lo, hi): 24 random bits each, the same float on the chip and on the host.
    float *uniform(size_t n, float lo, float hi)
    {
        float *b = zeros<float>(n);
        for (size_t i = 0; i < n; i++) {
            b[i] = lo + (hi - lo) * (static_cast<float>(rng_.next() >> 8) * (1.0f / 16777216.0f));
        }
        return b;
    }

    int below(int n) { return rng_.below(n); }

    void report(const void *bytes, size_t n) const { sink_->report(sink_->user, kernel_, index_, bytes, n); }

  private:
    static constexpr int kMaxBuffers = 24;
    const KernelSink *sink_;
    const char *kernel_;
    int index_;
    Rng rng_;
    void *buffers_[kMaxBuffers] = {};
    int count_ = 0;
};

void lut_s8(const KernelSink *sink, int index)
{
    Case c(sink, "lut_s8", index);
    const int n = index * 9 + c.below(4);
    auto *values = c.random<int8_t>(n);
    const auto *turned = c.random<int8_t>(256);
    ai_engine_lut_s8(values, n, turned);
    c.report(values, n);
}

void lut_s16(const KernelSink *sink, int index)
{
    Case c(sink, "lut_s16", index);
    const int shift = index;
    const int n = 5 + index * 7 + c.below(4);
    auto *values = c.random<int16_t>(n);
    const int entries = (1 << (16 - shift)) + 1;
    const auto *table = c.random<int16_t>(entries);
    ai_engine_lut_s16(values, n, table + (1 << (15 - shift)), shift);
    c.report(values, n * sizeof(int16_t));
}

void stage_sum(const KernelSink *sink, int index)
{
    Case c(sink, "stage_sum_row_s8", index);
    const int vectors = 1 + c.below(5);
    auto *row = c.random<int8_t>(vectors * kVector);
    const auto *x = c.random<int8_t>(vectors * kVector);
    const auto *weights = c.random<int8_t>(vectors * kVector);
    constexpr int32_t kShifts[] = {0, 1, 3, 7};
    const int32_t run[] = {vectors, kShifts[index % 4], index % 2};
    ai_engine_stage_sum_row_s8(row, x, weights, run);
    c.report(row, vectors * kVector);
}

void stem(const KernelSink *sink, int index, int rows)
{
    Case c(sink, rows == 3 ? "stem3_s8" : "stem3_top_s8", index);
    const int pixels = 1 + c.below(6);
    const int step = 6 + 3 * c.below(3);
    const int offset = c.below(static_cast<int>(kVector));
    const size_t row_bytes = (offset + pixels * step + 2 * kVector) / kVector * kVector;
    auto *input = c.random<int8_t>(3 * row_bytes);
    const auto *weights = c.random<int8_t>(rows * 9 * kVector);
    const auto *bias = c.random<int8_t>(kBiasBlock);
    auto *out = c.zeros<int8_t>(pixels * kVector);
    const int32_t run[] = {pixels,
                           step,
                           slot(input + offset),
                           slot(input + row_bytes + offset),
                           slot(input + 2 * row_bytes + offset),
                           slot(weights),
                           c.below(10),
                           slot(bias)};
    if (rows == 3) {
        ai_engine_stem3_s8(out, run);
    } else {
        ai_engine_stem3_top_s8(out, run);
    }
    c.report(out, pixels * kVector);
}

void weighted_stats(const KernelSink *sink, int index)
{
    Case c(sink, "weighted_stats_row_s8", index);
    const int groups = 1 + c.below(4);
    auto *sums = c.random<int16_t>(groups * 2 * kVector);
    const auto *x = c.random<int8_t>(groups * kVector);
    const auto *p = c.random<int8_t>(groups * kVector);
    const int32_t run[] = {groups, 1 + c.below(8), 1 + c.below(8), 1 + c.below(8)};
    ai_engine_weighted_stats_row_s8(sums, x, p, run);
    c.report(sums, groups * 2 * kVector * sizeof(int16_t));
}

void row_sums(const KernelSink *sink, int index)
{
    Case c(sink, "row_sums_s8", index);
    const int groups = 1 + c.below(4);
    auto *sums = c.random<int16_t>(groups * kVector);
    const auto *x = c.random<int8_t>(groups * kVector);
    ai_engine_row_sums_s8(sums, x, groups);
    c.report(sums, groups * kVector * sizeof(int16_t));
}

void centred_squares(const KernelSink *sink, int index)
{
    Case c(sink, "centred_squares_row_s8", index);
    const int groups = 1 + c.below(4);
    auto *sums = c.random<int16_t>(groups * kVector);
    const auto *x = c.random<int8_t>(groups * kVector);
    const auto *mean = c.random<int8_t>(groups * kVector);
    const int32_t run[] = {groups, 1 + c.below(8)};
    ai_engine_centred_squares_row_s8(sums, x, mean, run);
    c.report(sums, groups * kVector * sizeof(int16_t));
}

void softmax(const KernelSink *sink, int index)
{
    {
        Case c(sink, "softmax_sums_s8", index);
        const int rows = 1 + c.below(8);
        const int stride = 8 + c.below(24);
        const auto *x = c.random<int8_t>(rows * stride);
        const float *table = c.uniform(256, 0.0f, 1.0f);
        auto *sums = c.zeros<float>(8);
        ai_engine_softmax_sums_s8(sums, x, table, rows, stride);
        c.report(sums, 8 * sizeof(float));
    }
    {
        Case c(sink, "softmax_norm_s8", index);
        const int rows = 1 + c.below(8);
        const int32_t strides[] = {8 + c.below(24), 8 + c.below(24)};
        const auto *x = c.random<int8_t>(rows * strides[0]);
        const float *table = c.uniform(256, 0.0f, 1.0f);
        const float *scales = c.uniform(8, 1.0f, 160.0f);
        auto *y = c.random<int8_t>(rows * strides[1]);
        ai_engine_softmax_norm_s8(y, x, table, scales, rows, strides);
        c.report(y, rows * strides[1]);
    }
    {
        Case c(sink, "softmax_row_sums_s8", index);
        const int len = 1 + c.below(40);
        const int stride = len + c.below(8);
        const auto *x = c.random<int8_t>(4 * stride);
        const float *table = c.uniform(256, 0.0f, 1.0f);
        auto *sums = c.zeros<float>(4);
        ai_engine_softmax_row_sums_s8(sums, x, table, len, stride);
        c.report(sums, 4 * sizeof(float));
    }
}

// A band table of bands entries: band b's input b vectors in, its weights after the bands before it.
int32_t *band_table(Case &c, int bands, int lanes, int *weight_bytes)
{
    auto *table = c.zeros<int32_t>(3 * bands);
    int at = 0;
    for (int b = 0; b < bands; b++) {
        table[3 * b] = static_cast<int32_t>(b * kVector);
        table[3 * b + 1] = at;
        table[3 * b + 2] = 1 + c.below(3);
        at += table[3 * b + 2] * lanes * static_cast<int>(kVector);
    }
    *weight_bytes = at;
    return table;
}

void band_conv(const KernelSink *sink, int index)
{
    Case c(sink, "band_conv_s8", index);
    const int bands = 1 + c.below(4);
    int weight_bytes = 0;
    const int32_t *table = band_table(c, bands, 12, &weight_bytes);
    const int time_taps = 1 + c.below(3);
    const int8_t *columns[3] = {};
    for (int t = 0; t < time_taps; t++) {
        columns[t] = (t == time_taps - 1 && index % 2 == 1) ? nullptr : c.random<int8_t>((bands + 3) * kVector);
    }
    const auto *weights = c.random<int8_t>(time_taps * weight_bytes);
    const int out_stride = (1 + c.below(2)) * static_cast<int>(kVector);
    auto *out = c.zeros<int8_t>(bands * out_stride);
    const auto *residual = c.random<int8_t>(bands * out_stride);
    const int32_t run[] = {bands,       slot(table),          time_taps,       weight_bytes,
                           kVector,     out_stride,           1 + c.below(10), slot(c.random<int8_t>(kBiasBlock)),
                           index % 4,   slot(residual) - slot(out), index % 3 != 0};
    ai_engine_band_conv_s8(out, columns, weights, run);
    c.report(out, bands * out_stride);
}

void band_fast(const KernelSink *sink, int index, const char *kernel, int time_taps, int lanes, int residual)
{
    Case c(sink, kernel, index);
    const int bands = 1 + c.below(4);
    int weight_bytes = 0;
    const int32_t *table = band_table(c, bands, lanes, &weight_bytes);
    const int out_stride = (1 + c.below(2)) * static_cast<int>(kVector);
    auto *out = c.zeros<int8_t>(bands * out_stride);
    const auto *res = c.random<int8_t>(bands * out_stride);
    int32_t run[14] = {bands, slot(table), kVector, out_stride, c.below(10), slot(c.random<int8_t>(kBiasBlock)),
                       residual ? c.below(6) - 1 : 0, slot(res) - slot(out)};
    for (int t = 0; t < time_taps; t++) {
        run[8 + t] = slot(c.random<int8_t>((bands + 3) * kVector));
        run[11 + t] = slot(c.random<int8_t>(weight_bytes));
    }
    if (strcmp(kernel, "band_k1_s8") == 0) { ai_engine_band_k1_s8(out, run); }
    if (strcmp(kernel, "band_k1_res_s8") == 0) { ai_engine_band_k1_res_s8(out, run); }
    if (strcmp(kernel, "band_k1_linear_s8") == 0) { ai_engine_band_k1_linear_s8(out, run); }
    if (strcmp(kernel, "band_k3_s8") == 0) { ai_engine_band_k3_s8(out, run); }
    if (strcmp(kernel, "band_k3_res_s8") == 0) { ai_engine_band_k3_res_s8(out, run); }
    if (strcmp(kernel, "band_k3_l3_s8") == 0) { ai_engine_band_k3_l3_s8(out, run); }
    c.report(out, bands * out_stride);
}

void depthwise(const KernelSink *sink, int index)
{
    {
        Case c(sink, index % 2 ? "dw3x3_bias_row_s8" : "dw3x3_row_s8", index);
        const int pixels = 1 + c.below(5);
        const int vectors = 1 + c.below(3);
        const int width = vectors * kVector;
        const int8_t *rows[3] = {c.random<int8_t>((pixels + 2) * width), c.random<int8_t>((pixels + 2) * width),
                                 c.random<int8_t>((pixels + 2) * width)};
        const auto *taps = c.random<int8_t>(9 * width);
        auto *out = c.zeros<int8_t>(pixels * width);
        const int32_t run[] = {pixels, vectors, 1 + c.below(10), slot(c.random<int8_t>(vectors * kBiasBlock))};
        if (index % 2) {
            ai_engine_dw3x3_bias_row_s8(out, rows, taps, run);
        } else {
            ai_engine_dw3x3_row_s8(out, rows, taps, run);
        }
        c.report(out, pixels * width);
    }
    {
        Case c(sink, "spread_row_s8", index);
        const int pixels = 1 + c.below(9);
        const auto *in = c.random<int8_t>(pixels);
        auto *out = c.zeros<int8_t>(pixels * kVector);
        ai_engine_spread_row_s8(out, in, pixels);
        c.report(out, pixels * kVector);
    }
    {
        Case c(sink, "dwk_row_s8", index);
        const int pixels = 1 + c.below(5);
        const int vectors = 1 + c.below(3);
        const int k = index % 2 ? 4 : 2;
        const int width = vectors * kVector;
        const int8_t *rows[4] = {};
        for (int j = 0; j < k; j++) {
            rows[j] = c.random<int8_t>(pixels * width);
        }
        const auto *taps = c.random<int8_t>(vectors * k * kVector);
        auto *out = c.zeros<int8_t>(pixels * width);
        const int32_t run[] = {pixels, vectors, 1 + c.below(10), slot(c.random<int8_t>(vectors * kBiasBlock)), k};
        ai_engine_dwk_row_s8(out, rows, taps, run);
        c.report(out, pixels * width);
    }
    {
        Case c(sink, "pad_row_s8", index);
        const int pixels = 1 + c.below(5);
        const int whole = c.below(4);
        const int half = whole == 0 ? 1 : c.below(2);
        const int in_bytes = whole * kVector + half * 8;
        const int out_bytes = (whole + half) * kVector;
        const auto *in = c.random<int8_t>(pixels * in_bytes);
        auto *out = c.random<int8_t>(pixels * out_bytes);
        const int32_t run[] = {pixels, whole, half};
        ai_engine_pad_row_s8(out, in, run);
        c.report(out, pixels * out_bytes);
    }
    {
        Case c(sink, "dw_time_s8", index);
        const int rows = 1 + c.below(5);
        const int vectors = 1 + c.below(3);
        const int k = 3 + 2 * c.below(4);
        const int width = vectors * kVector;
        const auto *x = c.random<int8_t>((rows + k - 1) * width);
        const auto *taps = c.random<int8_t>(k * width);
        auto *out = c.zeros<int8_t>(rows * width);
        const int32_t run[] = {rows, vectors, k, 1 + c.below(10), slot(c.random<int8_t>(vectors * kBiasBlock))};
        ai_engine_dw_time_s8(out, x, taps, run);
        c.report(out, rows * width);
    }
}

void group_norm(const KernelSink *sink, int index)
{
    {
        Case c(sink, "gn_sums_s8", index);
        const int frames = 1 + c.below(6);
        const int width = 8 * (1 + c.below(4));
        const int groups = 1 + c.below(3);
        const int stride = groups * width + 8 * c.below(3);
        const auto *x = c.random<int8_t>(frames * stride);
        auto *sums = c.zeros<int32_t>(groups);
        const int32_t run[] = {frames, stride, width, groups};
        ai_engine_gn_sums_s8(sums, x, run);
        c.report(sums, groups * sizeof(int32_t));
    }
    {
        static const char *const kNames[] = {"gn_squares_s8", "gn_squares_x2_s8", "gn_squares_x4_s8"};
        const int repeat = index % 3;
        Case c(sink, kNames[repeat], index);
        const int frames = 1 + c.below(6);
        const int width = 8 * (1 + c.below(4));
        const int stride = 2 * width + c.below(16);
        const auto *x = c.random<int8_t>(frames * stride);
        const float *deviations = c.uniform(2 * 256, -4.0f, 4.0f);
        auto *squares = c.zeros<float>(2);
        const int32_t run[] = {frames, stride, width};
        if (repeat == 0) { ai_engine_gn_squares_s8(squares, x, deviations, run); }
        if (repeat == 1) { ai_engine_gn_squares_x2_s8(squares, x, deviations, run); }
        if (repeat == 2) { ai_engine_gn_squares_x4_s8(squares, x, deviations, run); }
        c.report(squares, 2 * sizeof(float));
    }
    {
        Case c(sink, "gn_outputs_s8", index);
        const int frames = 1 + c.below(4);
        const int width = 8 * (1 + c.below(4));
        const int groups = 2 * (1 + c.below(2));
        const int columns = groups * width;
        const int stride = columns + static_cast<int>(kVector) * c.below(2);
        const auto *x = c.random<int8_t>(frames * stride);
        const auto *tables = c.random<int8_t>(groups * 256);
        auto *y = c.random<int8_t>(frames * stride);
        const int32_t run[] = {frames,
                               columns,
                               width,
                               groups,
                               c.below(8),
                               slot(c.random<int8_t>(columns)),
                               slot(c.random<int8_t>(columns)),
                               slot(c.zeros<int8_t>(columns)),
                               stride};
        ai_engine_gn_outputs_s8(y, x, tables, run);
        c.report(y, frames * stride);
    }
}

void layernorm(const KernelSink *sink, int index)
{
    static const int kWidths[] = {8, 12, 16, 24, 48, 96, 4, 32};
    const int n = kWidths[index % 8];
    const float inv_n = 1.0f / static_cast<float>(n);
    const int32_t run[] = {n,
                           n / 4,
                           128 * n,
                           bits(inv_n),
                           bits(static_cast<float>(n)),
                           bits(inv_n * 0.0625f),
                           bits(1e-5f),
                           bits(0.03125f),
                           bits(0.0078125f),
                           bits(0.5f)};
    {
        Case c(sink, "ln_deviations_s8", index);
        const int pairs = 1 + c.below(3);
        const auto *x = c.random<int8_t>(2 * pairs * n);
        auto *out = c.zeros<float>(2 * pairs * n + 2 * pairs);
        ai_engine_ln_deviations_s8(out, out + 2 * pairs * n, x, pairs, run);
        c.report(out, (2 * pairs * n + 2 * pairs) * sizeof(float));
    }
    {
        Case c(sink, "ln_scales_s8", index);
        const int pairs = 1 + c.below(4);
        float *variances = c.uniform(2 * pairs, 0.125f, 4096.0f);
        variances[0] = index % 2 ? 4.0f : variances[0];
        auto *scales = c.zeros<float>(2 * pairs);
        ai_engine_ln_scales_s8(scales, variances, run, pairs);
        c.report(scales, 2 * pairs * sizeof(float));
    }
    {
        Case c(sink, "ln_outputs_s8", index);
        const int pairs = 1 + c.below(3);
        const float *dev = c.uniform(2 * pairs * n, -8.0f, 8.0f);
        const float *scales = c.uniform(2 * pairs, 0.01f, 2.0f);
        const float *gb = c.uniform(2 * n, -4.0f, 4.0f);
        auto *y = c.zeros<int8_t>(2 * pairs * n);
        ai_engine_ln_outputs_s8(y, dev, scales, gb, pairs, run);
        c.report(y, 2 * pairs * n);
    }
}

using RowKernel = void (*)(int8_t *, const int8_t *, const int8_t *, const int32_t *);

// A pointwise row case: rows of K inputs stride vectors apart, blocks of 16 outputs, the last last wide, past bytes
// after each row, the residual laid out as the output.
void pointwise(const KernelSink *sink, int index, const char *kernel, RowKernel kernel_fn, int fixed_k, int held)
{
    Case c(sink, kernel, index);
    const int rows = 1 + c.below(3);
    const int blocks = 1 + c.below(3);
    const int whole = fixed_k ? fixed_k / 16 - (fixed_k % 16 ? 0 : 1) : c.below(4);
    const int tail = fixed_k ? (fixed_k % 16 ? 1 : 0) : c.below(2);
    const int k = fixed_k ? fixed_k : 16 * whole + (tail ? 8 : 16);
    const int stride_vectors = (k + 15) / 16 + (held ? 0 : c.below(2));
    const int in_stride = held ? k : 16 * stride_vectors;
    const int last = held || index % 3 ? (c.below(2) ? 16 : 8) : 0;
    const int width = last == 8 ? 8 : 16;
    // A last block of 16 stores whole vectors, so its rows stay 16-byte aligned; halves need only 8.
    const int past = held ? 0 : (last == 16 ? 16 : 8) * c.below(3);
    const int row_bytes = 16 * (blocks - 1) + width + past;
    const auto *in = c.random<int8_t>(rows * in_stride + 16);
    const auto *filter = c.random<int8_t>(blocks * k * 16);
    auto *out = c.zeros<int8_t>(rows * row_bytes);
    const int32_t run[] = {rows,
                           blocks,
                           stride_vectors,
                           whole,
                           tail,
                           1 + c.below(12),
                           slot(c.random<int8_t>(blocks * kBiasBlock)),
                           slot(c.random<int8_t>(rows * row_bytes)),
                           1 + c.below(4),
                           last,
                           past};
    kernel_fn(out, in, filter, run);
    c.report(out, rows * row_bytes);
}

void pointwise_twelve(const KernelSink *sink, int index, int k)
{
    Case c(sink, k == 12 ? "pw_conv_k12_s8" : "pw_conv_k24_n12_s8", index);
    const int rows = 1 + c.below(4);
    const int blocks = k == 12 ? 1 + c.below(3) : 1;
    const int last = k == 12 ? (c.below(2) ? 16 : 8) : 12;
    const int row_bytes = 16 * (blocks - 1) + last;
    const auto *in = c.random<int8_t>(rows * k + 16);
    const auto *filter = c.random<int8_t>(blocks * k * 16);
    auto *out = c.zeros<int8_t>(rows * row_bytes);
    const int32_t run[] = {rows, blocks, 0, 0, 0, 1 + c.below(12), slot(c.random<int8_t>(blocks * kBiasBlock)), 0, 1,
                           k == 12 ? last : 16};
    if (k == 12) {
        ai_engine_pw_conv_k12_s8(out, in, filter, run);
    } else {
        ai_engine_pw_conv_k24_n12_s8(out, in, filter, run);
    }
    c.report(out, rows * row_bytes);
}

// ConvBlock's fused rows: norm (ReLU then a depthwise 1x1), residual (x added then ReLU) or bias (MatMul then ReLU
// then an int8 bias). fixed_c 24 or 48 holds a pixel whole; else K comes from the run or unrolled.
void fused(const KernelSink *sink, int index, const char *kernel, RowKernel kernel_fn, int k_inputs, int fixed_c,
           char kind)
{
    Case c(sink, kernel, index);
    const int pixels = 1 + c.below(3);
    const int blocks = fixed_c ? (fixed_c + 15) / 16 : 1 + c.below(3);
    const int whole = k_inputs ? 0 : c.below(4);
    const int tail = k_inputs ? 0 : c.below(2);
    const int k = fixed_c ? fixed_c : k_inputs ? k_inputs : 16 * whole + (tail ? 8 : 16);
    const int stride_vectors = (k + 15) / 16 + (fixed_c ? 0 : c.below(2));
    const int last = kind == 'n' || k_inputs ? 16 : (fixed_c ? (fixed_c % 16 ? 8 : 16) : (c.below(2) ? 16 : 8));
    const int out_bytes = kind == 'n' ? 16 * blocks : 16 * (blocks - 1) + last;
    const auto *in = c.random<int8_t>(pixels * 16 * stride_vectors + 16);
    const auto *filter = c.random<int8_t>(blocks * k * 16);
    auto *out = c.zeros<int8_t>(pixels * out_bytes);
    int32_t run[10] = {pixels, blocks, stride_vectors, whole, tail, 1 + c.below(12),
                       slot(c.random<int8_t>(blocks * kBiasBlock))};
    if (kind == 'n') {
        run[7] = slot(c.random<int8_t>(blocks * 16));
        run[8] = slot(c.random<int8_t>(blocks * kBiasBlock));
        run[9] = 1 + c.below(10);
    } else if (kind == 'r') {
        run[7] = slot(c.random<int8_t>(pixels * 16 * blocks));
        run[8] = strstr(kernel, "_x_") != nullptr ? 1 + c.below(4) : (k_inputs || fixed_c ? 0 : c.below(4));
        run[9] = last;
    } else {
        run[0] = pixels;
        run[6] = index % 3 ? slot(c.random<int8_t>(blocks * 16)) : 0;
        run[7] = index % 2;
        run[8] = last;
    }
    kernel_fn(out, in, filter, run);
    c.report(out, pixels * out_bytes);
}

void pointwise_all(const KernelSink *sink, int i)
{
    pointwise(sink, i, "pw_conv_rows_s8", ai_engine_pw_conv_rows_s8, 0, 0);
    pointwise(sink, i, "pw_add_rows_s8", ai_engine_pw_add_rows_s8, 0, 0);
    pointwise(sink, i, "pw_add_x_rows_s8", ai_engine_pw_add_x_rows_s8, 0, 0);
    pointwise(sink, i, "pw_conv_k24_s8", ai_engine_pw_conv_k24_s8, 24, 1);
    pointwise(sink, i, "pw_add_k24_s8", ai_engine_pw_add_k24_s8, 24, 1);
    pointwise(sink, i, "pw_add_x_k24_s8", ai_engine_pw_add_x_k24_s8, 24, 1);
    pointwise(sink, i, "pw_conv_k48_s8", ai_engine_pw_conv_k48_s8, 48, 1);
    pointwise(sink, i, "pw_add_k48_s8", ai_engine_pw_add_k48_s8, 48, 1);
    pointwise(sink, i, "pw_add_x_k48_s8", ai_engine_pw_add_x_k48_s8, 48, 1);
    pointwise(sink, i, "pw_conv_k72_s8", ai_engine_pw_conv_k72_s8, 72, 0);
    pointwise(sink, i, "pw_add_k72_s8", ai_engine_pw_add_k72_s8, 72, 0);
    pointwise(sink, i, "pw_add_x_k72_s8", ai_engine_pw_add_x_k72_s8, 72, 0);
    pointwise(sink, i, "pw_conv_k96_s8", ai_engine_pw_conv_k96_s8, 96, 0);
    pointwise(sink, i, "pw_add_k96_s8", ai_engine_pw_add_k96_s8, 96, 0);
    pointwise(sink, i, "pw_add_x_k96_s8", ai_engine_pw_add_x_k96_s8, 96, 0);
    pointwise(sink, i, "pw_conv_k128_s8", ai_engine_pw_conv_k128_s8, 128, 0);
    pointwise(sink, i, "pw_add_k128_s8", ai_engine_pw_add_k128_s8, 128, 0);
    pointwise(sink, i, "pw_add_x_k128_s8", ai_engine_pw_add_x_k128_s8, 128, 0);
    pointwise(sink, i, "pw_conv_k144_s8", ai_engine_pw_conv_k144_s8, 144, 0);
    pointwise(sink, i, "pw_add_k144_s8", ai_engine_pw_add_k144_s8, 144, 0);
    pointwise(sink, i, "pw_add_x_k144_s8", ai_engine_pw_add_x_k144_s8, 144, 0);
    pointwise(sink, i, "pw_conv_k192_s8", ai_engine_pw_conv_k192_s8, 192, 0);
    pointwise(sink, i, "pw_add_k192_s8", ai_engine_pw_add_k192_s8, 192, 0);
    pointwise(sink, i, "pw_add_x_k192_s8", ai_engine_pw_add_x_k192_s8, 192, 0);
    pointwise_twelve(sink, i, 12);
    pointwise_twelve(sink, i, 24);
    fused(sink, i, "pw_norm_row_s8", ai_engine_pw_norm_row_s8, 0, 0, 'n');
    fused(sink, i, "pw_residual_row_s8", ai_engine_pw_residual_row_s8, 0, 0, 'r');
    fused(sink, i, "pw_bias_row_s8", ai_engine_pw_bias_row_s8, 0, 0, 'b');
    fused(sink, i, "pw_norm_c24_s8", ai_engine_pw_norm_c24_s8, 0, 24, 'n');
    fused(sink, i, "pw_norm_c48_s8", ai_engine_pw_norm_c48_s8, 0, 48, 'n');
    fused(sink, i, "pw_residual_c24_s8", ai_engine_pw_residual_c24_s8, 0, 24, 'r');
    fused(sink, i, "pw_residual_c48_s8", ai_engine_pw_residual_c48_s8, 0, 48, 'r');
    fused(sink, i, "pw_residual_x_c24_s8", ai_engine_pw_residual_x_c24_s8, 0, 24, 'r');
    fused(sink, i, "pw_residual_x_c48_s8", ai_engine_pw_residual_x_c48_s8, 0, 48, 'r');
    fused(sink, i, "pw_norm_c96_s8", ai_engine_pw_norm_c96_s8, 96, 0, 'n');
    fused(sink, i, "pw_norm_c144_s8", ai_engine_pw_norm_c144_s8, 144, 0, 'n');
    fused(sink, i, "pw_norm_c192_s8", ai_engine_pw_norm_c192_s8, 192, 0, 'n');
    fused(sink, i, "pw_residual_c96_s8", ai_engine_pw_residual_c96_s8, 96, 0, 'r');
    fused(sink, i, "pw_residual_c144_s8", ai_engine_pw_residual_c144_s8, 144, 0, 'r');
    fused(sink, i, "pw_residual_c192_s8", ai_engine_pw_residual_c192_s8, 192, 0, 'r');
    fused(sink, i, "pw_residual_x_c96_s8", ai_engine_pw_residual_x_c96_s8, 96, 0, 'r');
    fused(sink, i, "pw_residual_x_c144_s8", ai_engine_pw_residual_x_c144_s8, 144, 0, 'r');
    fused(sink, i, "pw_residual_x_c192_s8", ai_engine_pw_residual_x_c192_s8, 192, 0, 'r');
}

} // namespace

void KERNEL_CASES(const KernelSink *sink)
{
    for (int i = 0; i < kCases; i++) {
        lut_s8(sink, i);
        lut_s16(sink, i);
        stage_sum(sink, i);
        stem(sink, i, 3);
        stem(sink, i, 2);
        weighted_stats(sink, i);
        row_sums(sink, i);
        centred_squares(sink, i);
        softmax(sink, i);
        band_conv(sink, i);
        band_fast(sink, i, "band_k1_s8", 1, 12, 0);
        band_fast(sink, i, "band_k1_res_s8", 1, 12, 1);
        band_fast(sink, i, "band_k1_linear_s8", 1, 12, 0);
        band_fast(sink, i, "band_k3_s8", 3, 12, 0);
        band_fast(sink, i, "band_k3_res_s8", 3, 12, 1);
        band_fast(sink, i, "band_k3_l3_s8", 3, 3, 0);
        depthwise(sink, i);
        group_norm(sink, i);
        layernorm(sink, i);
        pointwise_all(sink, i);
    }
}
