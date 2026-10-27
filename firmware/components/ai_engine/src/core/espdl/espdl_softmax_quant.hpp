/** Build SoftmaxQuant for one .espdl node: esp-dl's int8 Softmax and the QuantizeLinear reading it as one op,
 * same exp table, sums and rounding, int8 out (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built
 *  @ret the module, nullptr when the node is not int8
 */
#pragma once

#include <cstdint>
#include <string>

namespace dl::module {
class Module;
} // namespace dl::module

namespace fbs {
class FbsModel;
} // namespace fbs

namespace ai {

constexpr int kSoftmaxTableSize = 256;

dl::module::Module *softmax_quant_from(fbs::FbsModel *fbs_model, std::string node_name);

/** Fill table with esp-dl's int8 Softmax exp table on exponent, entry by the input's byte; through products,
 * the int8 each byte scales to (esp_ppq_patches.softmax_scale_from), when not null.
 *  @ctx task | non-blocking | caller owns table (kSoftmaxTableSize floats); takes a scratch table from PSRAM
 *  @ret false when the scratch table finds no memory
 */
bool softmax_table_s8(float *table, int exponent, const int8_t *products);

/** One block of esp-dl's int8 Softmax and the QuantizeLinear after it: each of inner columns of len rows
 * summed in order through table, then each value scaled by 1 / sum * out_rescale and rounded half up to int8.
 *  @ctx any | non-blocking | caller owns x, y, table and scales (inner floats)
 */
void softmax_block_s8(const int8_t *x, int8_t *y, int len, int inner, const float *table, float *scales,
                      float out_rescale) noexcept;

} // namespace ai
