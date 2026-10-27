/** Build LayerNormS8 for one .espdl node: esp-dl's int8 LayerNormalization over the last axis, the same
 * float32 steps, without its double mean and per-value rescaling (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built
 *  @ret the module, nullptr when the node is not int8 over the last axis
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

dl::module::Module *layernorm_s8_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
