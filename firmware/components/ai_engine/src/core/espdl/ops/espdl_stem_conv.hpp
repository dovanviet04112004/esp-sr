/** Build StemConv for one .espdl node: a log-mel map broadcast into C channels and their depthwise 3x3 with
 * bias, each integer as esp-dl computes it (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built
 *  @ret the module, nullptr when the node is not int8 or its shift is below 1
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

dl::module::Module *stem_conv_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
