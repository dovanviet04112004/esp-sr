/** An op's work in independent parts: all on the task running the graph, or with AI_ENGINE_CORES_TWO part 1
 * on phu_task on core 1 (KEHOACH 5.1, ADR-0027).
 *  @ctx task | the task running a graph, within one op's forward
 */
#pragma once

namespace ai {

/** Part `part` of an op's work, 0 <= part < split_parts(). */
using SplitPart = void (*)(void *ctx, int part);

/** The parts each op splits its work into: 1, or 2 with AI_ENGINE_CORES_TWO.
 *  @ctx any | non-blocking
 */
int split_parts() noexcept;

/** part(ctx, p) once for every p < split_parts(), returning when all are done. Part 1 goes to phu_task when
 * it is free and on the other core; the caller takes it back when phu_task has not started it by the end of
 * part 0.
 *  @ctx task | blocks only on a part phu_task has started | ctx outlives the call; parts touch disjoint state
 */
void split_run(SplitPart part, void *ctx) noexcept;

/** Enter, then leave, the critical section parts running at once share, held around a few register writes and
 * never across a wait; nothing with AI_ENGINE_CORES_ONE, whose parts never overlap.
 *  @ctx task | non-blocking | not nested
 */
void split_lock() noexcept;
void split_unlock() noexcept;

} // namespace ai
