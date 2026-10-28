/** Build BandChain for one .espdl node: a run of SwiftF0's single-frame residual blocks, a column of x
 * through every block in internal RAM, each integer as esp-dl gives it (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built; every BandChain shares
 * one scratch when the loan falls short, so one graph holding them runs at a time
 *  @ret the module, nullptr when the node is not int8 or a block's kernel, shifts or inputs are not the op's
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

dl::module::Module *band_chain_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
