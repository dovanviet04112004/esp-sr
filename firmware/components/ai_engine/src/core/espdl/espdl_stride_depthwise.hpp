/** Build StrideDepthwise for one .espdl node: an int8 depthwise convolution of k frames at a stride of k with
 * bias, each integer as esp-dl computes it (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built
 *  @ret the module, nullptr when the node is not int8, its stride not 2 or 4 or its shift below 1
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

dl::module::Module *stride_depthwise_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
