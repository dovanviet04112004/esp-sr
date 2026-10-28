/** Regions of PSRAM or flash an op will soon write or read, loaded into the dcache one after another in the
 * background by the ROM's preload while the op computes (KEHOACH 3.14, ADR-0026).
 *  @ctx task | the task running the graph, within one op's forward, which ends with finish
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <array>

namespace ai {

class Preload {
  public:
    Preload() noexcept = default;
    ~Preload();
    Preload(const Preload &) = delete;
    Preload &operator=(const Preload &) = delete;

    /** Queue a region after those queued; skipped when the queue is full, when it is not behind the dcache,
     * or when it outgrows a quarter of the dcache.
     *  @ctx task | non-blocking
     */
    void add(const void *p, size_t bytes) noexcept;

    /** End the region under way once it is loaded, then start the next queued one while no preload runs.
     *  @ctx task | non-blocking
     */
    void pump() noexcept;

    /** Wait for the region under way, end it, and drop the regions not started.
     *  @ctx task | spins until the region under way is loaded
     */
    void finish() noexcept;

  private:
    struct Region {
        uint32_t start;
        uint32_t bytes;
    };
    static constexpr int kRegions = 4;
    std::array<Region, kRegions> regions_ = {};
    int queued_ = 0;
    int next_ = 0;
    bool busy_ = false;
    uint32_t autoload_ = 0; // the dcache autoload state the preload under way suspended
};

} // namespace ai
