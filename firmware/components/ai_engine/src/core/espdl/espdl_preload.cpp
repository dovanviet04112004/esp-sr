#include "core/espdl/espdl_preload.hpp"

#include "core/espdl/espdl_split.hpp"
#include "esp32s3/rom/cache.h"
#include "sdkconfig.h"
#include "soc/soc.h"

namespace ai {

namespace {

constexpr uint32_t kLineBytes = CONFIG_ESP32S3_DATA_CACHE_LINE_SIZE;
constexpr uint32_t kMaxRegionBytes = CONFIG_ESP32S3_DATA_CACHE_SIZE / 4;

// The ROM's one preload engine serves both parts of an op: a region runs alone from its start to its end.
// Shared preloads touch it under split_lock only.
bool s_engine_busy = false;

bool behind_dcache(uint32_t at) noexcept
{
    return at >= SOC_EXTRAM_DATA_LOW && at < SOC_EXTRAM_DATA_HIGH;
}

uint32_t line_down(uint32_t at) noexcept
{
    return at & ~(kLineBytes - 1);
}

uint32_t line_up(uint32_t at) noexcept
{
    return line_down(at + kLineBytes - 1);
}

} // namespace

Preload::~Preload()
{
    finish();
}

void Preload::add(const void *p, size_t bytes) noexcept
{
    const uint32_t at = reinterpret_cast<uintptr_t>(p);
    if (bytes <= kMaxRegionBytes) { queue(line_down(at), line_up(at + bytes), false); }
}

void Preload::add_written(const void *p, size_t bytes) noexcept
{
    const uint32_t at = reinterpret_cast<uintptr_t>(p);
    if (bytes <= kMaxRegionBytes) { queue(line_up(at), line_down(at + bytes), true); }
}

void Preload::queue(uint32_t start, uint32_t end, bool written) noexcept
{
    if (queued_ == kRegions || end <= start || !behind_dcache(start)) { return; }
    regions_[queued_++] = {start, end - start, written};
}

void Preload::pump() noexcept
{
    if (busy_) {
        if (!Cache_DCache_Preload_Done()) { return; }
        end_region();
    }
    if (next_ == queued_) { return; }
    if (!shared_) {
        if (Cache_DCache_Preload_Done()) { start_region(); }
        return;
    }
    split_lock();
    if (!s_engine_busy && Cache_DCache_Preload_Done()) {
        start_region();
        s_engine_busy = true;
    }
    split_unlock();
}

void Preload::start_region() noexcept
{
    autoload_ = Cache_Start_DCache_Preload(regions_[next_].start, regions_[next_].bytes, 0);
    next_++;
    busy_ = true;
}

// A line the engine loads after the part wrote it would come back as PSRAM holds it, the write lost.
void Preload::settle_writes() noexcept
{
    if (busy_ && regions_[next_ - 1].written) {
        while (!Cache_DCache_Preload_Done()) {}
        end_region();
    }
    int kept = next_;
    for (int i = next_; i < queued_; i++) {
        if (!regions_[i].written) { regions_[kept++] = regions_[i]; }
    }
    queued_ = kept;
}

void Preload::finish() noexcept
{
    if (busy_) {
        while (!Cache_DCache_Preload_Done()) {}
        end_region();
    }
    queued_ = 0;
    next_ = 0;
}

void Preload::end_region() noexcept
{
    if (!shared_) {
        Cache_End_DCache_Preload(autoload_);
        busy_ = false;
        return;
    }
    split_lock();
    Cache_End_DCache_Preload(autoload_);
    s_engine_busy = false;
    split_unlock();
    busy_ = false;
}

} // namespace ai
