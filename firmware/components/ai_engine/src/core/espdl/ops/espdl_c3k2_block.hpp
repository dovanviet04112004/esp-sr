/** Build C3k2Block for one .espdl node: YOLO's C3k2 of one 3x3 bottleneck, its 1x1 ConvLuts, Slices, Add and
 * Concat, a row at a time on esp-dl's kernels with every middle row in the cache (ADR-0036, KEHOACH 3.18).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built
 *  @ret the module, nullptr when the node is no int8 block or lacks a table or an exponent
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

dl::module::Module *c3k2_block_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
