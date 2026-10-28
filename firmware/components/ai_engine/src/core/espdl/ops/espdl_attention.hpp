/** Build AttentionCore for one .espdl node: a ReDimNet2 attention's heads, scores, softmax and weighed
 * values, each integer as esp-dl computes it (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built
 *  @ret the module, nullptr when the node is not int8 or lacks its heads and shifts
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

dl::module::Module *attention_core_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
