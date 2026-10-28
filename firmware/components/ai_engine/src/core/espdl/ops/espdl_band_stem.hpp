/** Build BandStem for one .espdl node: SwiftF0's first convolution, three input channels to 16, each integer
 * as esp-dl gives it (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built; every BandStem shares one
 * scratch when the loan falls short, so one graph holding them runs at a time
 *  @ret the module, nullptr when the node is not int8 or its shift or lanes are not the op's
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

dl::module::Module *band_stem_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
