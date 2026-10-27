/** Build StageSum for one .espdl node: a chain of esp-dl's int8 Adds summing per-channel Muls of [N, T, C]
 * maps, one row at a time, each product rounded and each sum saturated as esp-dl's kernels do (KEHOACH 3.14
 * step 2.5).
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

dl::module::Module *stage_sum_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
