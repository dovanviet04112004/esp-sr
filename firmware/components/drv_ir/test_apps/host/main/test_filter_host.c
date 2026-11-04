#include <assert.h>
#include <stdio.h>

#include "drv_ir/filter.h"

#define RELEASE_US 300000
#define CHATTER_US 3000

static void chatter(drv_ir_filter_t *filter, int64_t from_us, int edges)
{
    for (int i = 0; i < edges; i++) {
        drv_ir_filter_edge(filter, from_us + (int64_t)i * CHATTER_US, i % 2 == 0);
    }
}

static void test_a_burst_of_edges_is_one_hit(void)
{
    drv_ir_filter_t filter;
    drv_ir_filter_reset(&filter, RELEASE_US);
    chatter(&filter, 0, 100);
    assert(filter.hits == 1);
}

static void test_a_quiet_gap_lets_the_next_burst_count(void)
{
    drv_ir_filter_t filter;
    drv_ir_filter_reset(&filter, RELEASE_US);
    chatter(&filter, 0, 10);
    chatter(&filter, 10 * CHATTER_US + RELEASE_US, 10);
    assert(filter.hits == 2);
}

static void test_going_back_high_after_a_long_hold_is_no_new_hit(void)
{
    drv_ir_filter_t filter;
    drv_ir_filter_reset(&filter, RELEASE_US);
    assert(drv_ir_filter_edge(&filter, 0, true));
    assert(!drv_ir_filter_edge(&filter, 5 * RELEASE_US, false));
    assert(filter.hits == 1);
}

static void test_activity_ends_a_release_after_the_last_edge(void)
{
    drv_ir_filter_t filter;
    drv_ir_filter_reset(&filter, RELEASE_US);
    assert(!drv_ir_filter_active(&filter, 0));
    drv_ir_filter_edge(&filter, 1000, true);
    assert(drv_ir_filter_active(&filter, 1000 + RELEASE_US - 1));
    assert(!drv_ir_filter_active(&filter, 1000 + RELEASE_US));
}

static void test_negative_control_without_a_release_every_fall_counts(void)
{
    drv_ir_filter_t filter;
    drv_ir_filter_reset(&filter, 0);
    chatter(&filter, 0, 100);
    assert(filter.hits == 50);
}

int main(void)
{
    test_a_burst_of_edges_is_one_hit();
    test_a_quiet_gap_lets_the_next_burst_count();
    test_going_back_high_after_a_long_hold_is_no_new_hit();
    test_activity_ends_a_release_after_the_last_edge();
    test_negative_control_without_a_release_every_fall_counts();
    puts("ir filter: all passed");
    return 0;
}
