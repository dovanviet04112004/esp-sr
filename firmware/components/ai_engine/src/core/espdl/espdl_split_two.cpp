#include <atomic>
#include <cstdint>

#include "ai_engine.h"
#include "core/espdl/espdl_split.hpp"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

namespace ai {

namespace {

constexpr int kParts = 2;
constexpr int kHelperPart = 1;

enum State : uint32_t { kIdle = 0, kPosted, kTaken, kDone };

struct Job {
    SplitPart part;
    void *ctx;
};

// s_split of KEHOACH 5.3: the job is the holder's alone until it frees s_held; s_state passes it over.
Job s_job = {};
std::atomic<uint32_t> s_state{kIdle};
std::atomic<uint32_t> s_held{0};
std::atomic<TaskHandle_t> s_helper{nullptr};
std::atomic<int32_t> s_helper_core{-1};
portMUX_TYPE s_parts_lock = portMUX_INITIALIZER_UNLOCKED;

bool hand_over() noexcept
{
    TaskHandle_t helper = s_helper.load(std::memory_order_acquire);
    // A caller on phu_task's own core would spin while phu_task, below it, never ran.
    if (helper == nullptr || s_helper_core.load(std::memory_order_relaxed) == xPortGetCoreID()) {
        return false;
    }
    uint32_t free = 0;
    return s_held.compare_exchange_strong(free, 1, std::memory_order_acquire);
}

} // namespace

int split_parts() noexcept
{
    return kParts;
}

void split_run(SplitPart part, void *ctx) noexcept
{
    if (!hand_over()) {
        for (int p = 0; p < kParts; p++) {
            part(ctx, p);
        }
        return;
    }
    s_job = {part, ctx};
    s_state.store(kPosted, std::memory_order_release);
    xTaskNotifyGive(s_helper.load(std::memory_order_relaxed));
    part(ctx, 0);
    uint32_t posted = kPosted;
    if (s_state.compare_exchange_strong(posted, kIdle, std::memory_order_acq_rel)) {
        part(ctx, kHelperPart);
    } else {
        while (s_state.load(std::memory_order_acquire) != kDone) {}
        s_state.store(kIdle, std::memory_order_relaxed);
    }
    s_held.store(0, std::memory_order_release);
}

void split_lock() noexcept
{
    portENTER_CRITICAL(&s_parts_lock);
}

void split_unlock() noexcept
{
    portEXIT_CRITICAL(&s_parts_lock);
}

} // namespace ai

extern "C" void ai_engine_helper_serve(uint32_t wait_ms)
{
    using namespace ai;
    if (s_helper.load(std::memory_order_relaxed) == nullptr) {
        s_helper_core.store(xPortGetCoreID(), std::memory_order_relaxed);
        s_helper.store(xTaskGetCurrentTaskHandle(), std::memory_order_release);
    }
    if (ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(wait_ms)) == 0) { return; }
    uint32_t posted = kPosted;
    // A notice the caller outran finds its part taken back, or the next job posted.
    if (!s_state.compare_exchange_strong(posted, kTaken, std::memory_order_acq_rel)) { return; }
    s_job.part(s_job.ctx, kHelperPart);
    s_state.store(kDone, std::memory_order_release);
}
