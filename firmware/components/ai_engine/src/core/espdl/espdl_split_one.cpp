#include "core/espdl/espdl_split.hpp"

namespace ai {

int split_parts() noexcept
{
    return 1;
}

void split_run(SplitPart part, void *ctx) noexcept
{
    part(ctx, 0);
}

void split_lock() noexcept
{}

void split_unlock() noexcept
{}

} // namespace ai
