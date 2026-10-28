/** Build LayerNormS8 for one .espdl node: esp-dl's int8 LayerNormalization over the last axis, the same
 * float32 steps, without its double mean and per-value rescaling (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built
 *  @ret the module, nullptr when the node is not int8 over the last axis
 */
#pragma once

#include <array>
#include <cstdint>
#include <string>

namespace dl::module {
class Module;
} // namespace dl::module

namespace fbs {
class FbsModel;
} // namespace fbs

namespace ai {

dl::module::Module *layernorm_s8_from(fbs::FbsModel *fbs_model, std::string node_name);

/** layernorm_as_espdl's float32 steps over positions of n int8 values each, LayerNormS8's paired kernels
 * where n is a multiple of 4 up to 512; LayerNormS8 and StemConv's joined norm run it.
 *  @ctx task | init allocates PSRAM, run non-blocking | one owner, one run at a time
 */
class LayerNormRows {
  public:
    LayerNormRows() noexcept = default;
    ~LayerNormRows();
    LayerNormRows(const LayerNormRows &) = delete;
    LayerNormRows &operator=(const LayerNormRows &) = delete;

    /** Gamma and beta as int8 times their scales, beta null for none; x and y on their exponents.
     *  @ctx task | allocates PSRAM, once
     *  @ret false when PSRAM holds no room for the floats
     */
    bool init(int n, const int8_t *gamma, float gamma_scale, const int8_t *beta, float beta_scale,
              int x_exponent, int y_exponent, float epsilon) noexcept;

    /** The positions of x normalised into y, which may be x: each value read ahead of the write over it.
     *  @ctx task | non-blocking | after init
     */
    void run(const int8_t *xs, int8_t *ys, int positions) const noexcept;

  private:
    static constexpr int kRunSlots = 10;

    void run_pairs(const int8_t *xs, int8_t *ys, int positions) const noexcept;
    void run_position(const int8_t *x, int8_t *y) const noexcept;

    float epsilon_ = 0.0f;
    int n_ = 0;
    float in_scale_ = 0.0f;
    float per_value_ = 0.0f;
    float out_rescale_ = 0.0f;
    float count_ = 0.0f;
    float inverse_n_ = 0.0f;
    float *gamma_ = nullptr; // one allocation holds every buffer below
    float *beta_ = nullptr;
    float *deviations_ = nullptr;
    float *variances_ = nullptr;
    float *scales_ = nullptr;
    float *gb_ = nullptr; // gamma, beta interleaved, times out_rescale
    std::array<int32_t, kRunSlots> run_ = {};
    bool float_mean_ = false;
    bool paired_ = false;
};

} // namespace ai
