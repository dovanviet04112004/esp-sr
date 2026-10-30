/** Build ConvLut for one .espdl node: esp-dl's own convolution kernels a band of rows at a time, each band
 *  then looked up in the activation's table while it is in the cache (ADR-0031, KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built
 *  @ret the module, nullptr when the node is no int8 or int16 2-d convolution or lacks its conv_exponent
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

dl::module::Module *conv_lut_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
