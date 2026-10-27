/** Register ai_engine's ops of KEHOACH 3.14 step 2.5 beside esp-dl's own, so a graph exported with them
 * builds.
 *  @ctx task | non-blocking | ahead of each dl::Model's build, from the task building it; the map is esp-dl's
 *  global
 */
#pragma once

namespace ai {

void register_espdl_ops() noexcept;

} // namespace ai
