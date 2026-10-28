#include "core/espdl/runtime/espdl_loan.hpp"

#include <algorithm>

#include "esp_heap_caps.h"
#include "sdkconfig.h"

namespace ai {

namespace {

constexpr size_t kLoanAlignBytes = 16; // the ops' vector loads
constexpr uint32_t kLoanCaps = MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT;
constexpr size_t kAllocSlackBytes = 64; // aligned_alloc's block header and padding

Loan s_loan = {nullptr, 0};
size_t s_asked = 0;

size_t lendable_bytes(size_t asked)
{
    // The reserve stays free for the other tasks' allocations while the graph runs (KEHOACH 6.5).
    const size_t kept = CONFIG_AI_ENGINE_LOAN_RESERVE_BYTES + kAllocSlackBytes;
    const size_t largest = heap_caps_get_largest_free_block(kLoanCaps);
    return largest > kept ? std::min(asked, largest - kept) & ~(kLoanAlignBytes - 1) : 0;
}

} // namespace

void loan_ask(size_t bytes) noexcept
{
    s_asked = std::max(s_asked, bytes);
}

size_t loan_take_asked() noexcept
{
    const size_t bytes = s_asked;
    s_asked = 0;
    return bytes;
}

Loan loan_lent() noexcept
{
    return s_loan;
}

WeightLoan::WeightLoan(size_t bytes) noexcept
{
    if (bytes == 0) {
        granted_ = true;
        return;
    }
    const size_t lent = lendable_bytes(bytes);
    mem_ = lent > 0 ? heap_caps_aligned_alloc(kLoanAlignBytes, lent, kLoanCaps) : nullptr;
    lent_ = mem_ != nullptr ? lent : 0;
    granted_ = lent_ == bytes;
    if (mem_ != nullptr) { s_loan = {static_cast<int8_t *>(mem_), lent_}; }
}

WeightLoan::~WeightLoan()
{
    s_loan = {nullptr, 0};
    heap_caps_free(mem_);
}

} // namespace ai
