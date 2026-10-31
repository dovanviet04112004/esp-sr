/** Build UpsampleConcatConv for one .espdl node: a 1x1 ConvLut over the Concat of a map upsampled twice by
 * nearest neighbour and another map, the Concat built a row at a time in the cache (ADR-0037, KEHOACH 3.18).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built
 *  @ret the module, nullptr when the node is no int8 1x1 convolution with a table and its exponent
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

dl::module::Module *upsample_concat_conv_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
