/** The detect branch's graph for ai_engine's test app, which holds its outputs to ESP-PPQ's (KEHOACH 3.18).
 *  @ctx the task that runs detect | non-blocking | nullptr until ai_engine_detect_load
 */
#pragma once

namespace dl {
class Model;
} // namespace dl

namespace ai {

dl::Model *detect_model() noexcept;

} // namespace ai
