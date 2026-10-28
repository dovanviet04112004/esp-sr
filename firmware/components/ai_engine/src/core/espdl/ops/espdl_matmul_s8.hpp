/** Build MatMulS8 for one .espdl node: an int8 MatMul of a map's rows by constant weights and the bias Add
 * after it, each row's integers as esp-dl computes them (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built
 *  @ret the module, nullptr when the node is not int8 or lacks its shift and output count
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

dl::module::Module *matmul_s8_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
