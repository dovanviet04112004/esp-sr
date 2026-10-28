/** Build BandHead for one .espdl node: SwiftF0's head of three convolutions and a maximum over the bands,
 * each integer as esp-dl gives it (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built; every BandHead shares one
 * scratch when the loan falls short, so one graph holding them runs at a time
 *  @ret the module, nullptr when the node is not int8 or its shifts or bands are not the op's
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

dl::module::Module *band_head_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
