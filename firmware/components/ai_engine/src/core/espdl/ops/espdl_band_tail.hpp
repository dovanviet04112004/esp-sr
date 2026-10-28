/** Build BandTail for one .espdl node: the tail of SwiftF0's confidence branch, streaming, each integer as
 * esp-dl computes it (KEHOACH 3.11 step 3.2).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built; the module keeps its
 * columns between runs until reset
 *  @ret the module, nullptr when the node is not an int8 tail of a lag, delay and shifts the op runs
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

dl::module::Module *band_tail_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
