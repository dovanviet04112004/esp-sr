/** The detect branch's graph for ai_engine's test app, which holds its outputs to ESP-PPQ's (KEHOACH 3.18).
 *  @ctx the task that runs detect | non-blocking | nullptr until ai_engine_detect_load
 */
#pragma once

#include <stddef.h>

namespace dl {
class Model;
} // namespace dl

namespace ai {

dl::Model *detect_model() noexcept;

/** The internal RAM a run of detect_model lends its ops, for the test app's profile run (KEHOACH 6.5). */
size_t detect_loan_bytes() noexcept;

} // namespace ai
