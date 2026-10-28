/** Build BandBlock for one .espdl node: a SwiftF0 residual block, two int8 convolutions along the bands, the
 * residual Add and its ReLU, each integer as esp-dl computes it (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built; every BandBlock shares
 * one scratch when the loan falls short, so one graph holding them runs at a time
 *  @ret the module, nullptr when the node is not an int8 block of a kernel and inputs the op runs
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

dl::module::Module *band_block_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
