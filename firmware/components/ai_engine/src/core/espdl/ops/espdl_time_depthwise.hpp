/** Build TimeDepthwise for one .espdl node: an int8 Linear depthwise convolution along time with bias, each
 * integer as esp-dl computes it (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built; every TimeDepthwise
 * shares one scratch, so one graph holding them runs at a time
 *  @ret the module, nullptr when the node is not int8 or lacks its shift
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

dl::module::Module *time_depthwise_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
