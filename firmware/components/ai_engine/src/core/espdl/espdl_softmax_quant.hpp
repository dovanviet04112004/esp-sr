/** Build SoftmaxQuant for one .espdl node: esp-dl's int8 Softmax and the QuantizeLinear reading it as one op,
 * same exp table, sums and rounding, int8 out (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built
 *  @ret the module, nullptr when the node is not int8
 */
#pragma once

#include <string>

namespace dl::module {
class Module;
} // namespace dl::module

namespace fbs {
class FbsModel;
} // namespace fbs

namespace ai {

dl::module::Module *softmax_quant_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
