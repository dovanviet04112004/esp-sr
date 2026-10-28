/** Build GroupNormS8 for one .espdl node: a GroupNorm chain of ReDimNet2's agg_gnorm on its [T, C] map, each
 * group a LayerNorm as esp-dl takes it, then the per-channel Mul and Add, every integer as they give it
 * (KEHOACH 3.14).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built
 *  @ret the module, nullptr when the node is not int8 or lacks its groups and shifts
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

dl::module::Module *group_norm_s8_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
