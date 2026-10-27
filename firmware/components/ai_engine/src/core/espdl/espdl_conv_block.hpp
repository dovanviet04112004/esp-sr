/** Build ConvBlock for one .espdl node: a ReDimNet2 conv block of int8 esp-dl ops run a time row at a time,
 * its middle tensors held in rows, each op's integers as esp-dl computes them (KEHOACH 3.14 step 2.5).
 *  @ctx task | non-blocking | esp-dl's creator calls it while a dl::Model is built
 *  @ret the module, nullptr when the node is not int8 or its shifts are not six
 */
#pragma once

#include <stddef.h>

#include <string>

namespace dl::module {
class Module;
} // namespace dl::module

namespace fbs {
class FbsModel;
} // namespace fbs

namespace ai {

dl::module::Module *conv_block_from(fbs::FbsModel *fbs_model, std::string node_name);

/** The most internal RAM a ConvBlock built since the last call borrows (KEHOACH 6.5); resets it to 0.
 *  @ctx task | non-blocking | the task building a graph, right after the run that builds its modules
 */
size_t conv_block_take_loan_bytes() noexcept;

/** Internal RAM every ConvBlock borrows while this lives, bytes as conv_block_take_loan_bytes gave them; with
 * none to be had, each block reads its weights in place (KEHOACH 6.5).
 *  @ctx task | allocates, non-blocking | the task running the graph, around a run; one loan at a time
 */
class ConvBlockLoan {
  public:
    explicit ConvBlockLoan(size_t bytes) noexcept;
    ~ConvBlockLoan();
    ConvBlockLoan(const ConvBlockLoan &) = delete;
    ConvBlockLoan &operator=(const ConvBlockLoan &) = delete;
    /** Whether the blocks got all the bytes they asked for. */
    bool granted() const noexcept
    {
        return granted_;
    }

  private:
    void *mem_ = nullptr;
    bool granted_ = false;
};

} // namespace ai
