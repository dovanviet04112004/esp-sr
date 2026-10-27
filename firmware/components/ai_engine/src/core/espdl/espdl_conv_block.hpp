/** Build ConvBlock for one .espdl node: a ReDimNet2 conv block of int8 esp-dl ops run a time row at a time,
 * its middle tensors held in rows, each op's integers as esp-dl computes them (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built
 *  @ret the module, nullptr when the node is not int8 or its shifts are not six
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

dl::module::Module *conv_block_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
