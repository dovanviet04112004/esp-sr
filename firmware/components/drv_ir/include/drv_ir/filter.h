/** The infrared output's chatter into hits, with no OS under it (KEHOACH 3.19).
 *  @ctx one context at a time | non-blocking | the caller owns the state
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int64_t release_us; // quiet this long ends a hit
    int64_t last_edge_us;
    uint32_t hits; // begun since the reset
    bool started;  // an edge has come
} drv_ir_filter_t;

/** Start with no hit.
 *  @ctx any | non-blocking
 */
void drv_ir_filter_reset(drv_ir_filter_t *filter, int64_t release_us);

/** Take one edge; low says the output went down, which is the only edge that can begin a hit.
 *  @ctx isr-safe | non-blocking
 *  @ret true when it began a hit
 */
bool drv_ir_filter_edge(drv_ir_filter_t *filter, int64_t now_us, bool low);

/** Whether the output has chattered within the release time.
 *  @ctx any | non-blocking
 */
bool drv_ir_filter_active(const drv_ir_filter_t *filter, int64_t now_us);

#ifdef __cplusplus
}
#endif
