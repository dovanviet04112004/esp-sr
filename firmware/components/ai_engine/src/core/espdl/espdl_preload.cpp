#include "core/espdl/espdl_preload.hpp"

#include "esp32s3/rom/cache.h"
#include "sdkconfig.h"
#include "soc/soc.h"

namespace ai {

namespace {

constexpr uint32_t kLineBytes = CONFIG_ESP32S3_DATA_CACHE_LINE_SIZE;
constexpr uint32_t kMaxRegionBytes = CONFIG_ESP32S3_DATA_CACHE_SIZE / 4;

bool behind_dcache(uint32_t at) noexcept
{
    return at >= SOC_EXTRAM_DATA_LOW && at < SOC_EXTRAM_DATA_HIGH;
}

} // namespace

Preload::~Preload()
{
    finish();
}

void Preload::add(const void *p, size_t bytes) noexcept
{
    const uint32_t at = reinterpret_cast<uintptr_t>(p);
    if (queued_ == kRegions || bytes == 0 || bytes > kMaxRegionBytes || !behind_dcache(at)) { return; }
    const uint32_t start = at & ~(kLineBytes - 1);
    const uint32_t end = (at + bytes + kLineBytes - 1) & ~(kLineBytes - 1);
    regions_[queued_++] = {start, end - start};
}

void Preload::pump() noexcept
{
    if (busy_) {
        if (!Cache_DCache_Preload_Done()) { return; }
        Cache_End_DCache_Preload(autoload_);
        busy_ = false;
    }
    // Another task's preload may be under way: the engine runs one region at a time.
    if (next_ < queued_ && Cache_DCache_Preload_Done()) {
        autoload_ = Cache_Start_DCache_Preload(regions_[next_].start, regions_[next_].bytes, 0);
        next_++;
        busy_ = true;
    }
}

void Preload::finish() noexcept
{
    if (busy_) {
        while (!Cache_DCache_Preload_Done()) {}
        Cache_End_DCache_Preload(autoload_);
        busy_ = false;
    }
    queued_ = 0;
    next_ = 0;
}

} // namespace ai
