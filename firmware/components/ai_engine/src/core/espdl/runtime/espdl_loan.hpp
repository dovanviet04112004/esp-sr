/** Internal RAM ai_engine's own ops borrow for their weights while a graph runs (KEHOACH 6.5, ADR-0025): ops
 * ask as they are built, and EspdlNet lends the largest ask over each run.
 *  @ctx task | the task that builds and runs the graph; one graph runs on a loan at a time
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

namespace ai {

/** The internal RAM lent to the run under way; mem nullptr and bytes 0 when none. */
struct Loan {
    int8_t *mem;
    size_t bytes;
};

/** Ask for bytes of internal RAM in every run of the graph being built.
 *  @ctx task | non-blocking | an op's build, within the run that builds the graph
 */
void loan_ask(size_t bytes) noexcept;

/** The largest ask since the last call, then 0.
 *  @ctx task | non-blocking | the task building a graph, right after the run that builds its modules
 */
size_t loan_take_asked() noexcept;

/** The internal RAM lent to the run under way.
 *  @ctx task | non-blocking | an op's forward
 */
Loan loan_lent() noexcept;

/** Internal RAM lent to the ops while this lives, bytes as loan_take_asked gave them; with none to be had,
 * every op reads its weights in place.
 *  @ctx task | allocates, non-blocking | the task running the graph, around a run; one loan at a time
 */
class WeightLoan {
  public:
    explicit WeightLoan(size_t bytes) noexcept;
    ~WeightLoan();
    WeightLoan(const WeightLoan &) = delete;
    WeightLoan &operator=(const WeightLoan &) = delete;
    /** Whether the ops got all the bytes they asked for. */
    bool granted() const noexcept
    {
        return granted_;
    }

  private:
    void *mem_ = nullptr;
    bool granted_ = false;
};

} // namespace ai
