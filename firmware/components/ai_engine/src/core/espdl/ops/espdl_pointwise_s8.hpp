/** Build PointwiseS8 for one .espdl node: an int8 1x1 convolution of a map's rows, and the residual Add after
 *  it if joined, each row's integers as esp-dl computes them (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built
 *  @ret the module, nullptr when the node is not int8 or lacks its shifts and output count
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

dl::module::Module *pointwise_s8_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
