/** Build WeightedStats for one .espdl node: an attentive pool's weighted sums of x and of x * x over time,
 * the Softmax over time of the weights too when it carries prob_exponent, each integer as esp-dl computes it
 * (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built
 *  @ret the module, nullptr when the node is not int8 or lacks its five shifts
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

dl::module::Module *weighted_stats_from(fbs::FbsModel *fbs_model, std::string node_name);

} // namespace ai
