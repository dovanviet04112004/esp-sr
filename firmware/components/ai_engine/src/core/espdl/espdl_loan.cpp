#include "core/espdl/espdl_loan.hpp"

#include <algorithm>

#include "esp_heap_caps.h"

namespace ai {

namespace {

constexpr size_t kLoanAlignBytes = 16; // the ops' vector loads

Loan s_loan = {nullptr, 0};
size_t s_asked = 0;

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
    mem_ = heap_caps_aligned_alloc(kLoanAlignBytes, bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    granted_ = mem_ != nullptr;
    if (granted_) { s_loan = {static_cast<int8_t *>(mem_), bytes}; }
}

WeightLoan::~WeightLoan()
{
    s_loan = {nullptr, 0};
    heap_caps_free(mem_);
}

} // namespace ai
