#include "drv_ir/filter.h"

void drv_ir_filter_reset(drv_ir_filter_t *filter, int64_t release_us)
{
    *filter = (drv_ir_filter_t){.release_us = release_us};
}

bool drv_ir_filter_active(const drv_ir_filter_t *filter, int64_t now_us)
{
    return filter->started && now_us - filter->last_edge_us < filter->release_us;
}

bool drv_ir_filter_edge(drv_ir_filter_t *filter, int64_t now_us, bool low)
{
    const bool quiet = !drv_ir_filter_active(filter, now_us);
    filter->last_edge_us = now_us;
    filter->started = true;
    if (!low || !quiet) { return false; }
    filter->hits++;
    return true;
}
