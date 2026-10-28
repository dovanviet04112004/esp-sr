/** Regions of PSRAM or flash an op will soon write or read, loaded into the dcache one after another in the
 * background by the ROM's preload while the op computes (KEHOACH 3.14, ADR-0026); both cores share the
 * engine.
 *  @ctx task | one part of an op's forward, which ends with finish; one instance a part
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include <array>

namespace ai {

class Preload {
  public:
    /** shared: another part of the op may preload at the same time, on the other core (ADR-0027). */
    explicit Preload(bool shared) noexcept : shared_(shared)
    {}
    ~Preload();
    Preload(const Preload &) = delete;
    Preload &operator=(const Preload &) = delete;

    /** Queue a region this part will read after those queued; skipped when the queue is full, when it is not
     * behind the dcache, or when it outgrows a quarter of the dcache.
     *  @ctx task | non-blocking
     */
    void add(const void *p, size_t bytes) noexcept;

    /** Queue a region this part will write, as add does, but only the dcache lines wholly inside it: another
     * part may be writing its edge lines. Call settle_writes ahead of the first write to it.
     *  @ctx task | non-blocking
     */
    void add_written(const void *p, size_t bytes) noexcept;

    /** End the region under way once it is loaded, then start the next queued one while no preload runs.
     *  @ctx task | non-blocking
     */
    void pump() noexcept;

    /** Ahead of writing the regions add_written queued: wait for one under way, drop those not started.
     *  @ctx task | spins until a written region under way is loaded
     */
    void settle_writes() noexcept;

    /** Wait for the region under way, end it, and drop the regions not started.
     *  @ctx task | spins until the region under way is loaded
     */
    void finish() noexcept;

  private:
    struct Region {
        uint32_t start;
        uint32_t bytes;
        bool written;
    };
    static constexpr int kRegions = 4;

    void queue(uint32_t start, uint32_t end, bool written) noexcept;
    void start_region() noexcept;
    void end_region() noexcept;

    std::array<Region, kRegions> regions_ = {};
    int queued_ = 0;
    int next_ = 0;
    bool busy_ = false;
    bool shared_ = false;
    uint32_t autoload_ = 0; // the dcache autoload state the preload under way suspended
};

} // namespace ai
