/** heap_caps_* mapped onto the C heap: on the host every capability is the same memory.
 *  @ctx any | non-blocking
 */
#pragma once

#include <stdlib.h>

#define MALLOC_CAP_8BIT (1u << 2)
#define MALLOC_CAP_INTERNAL (1u << 11)
#define MALLOC_CAP_SPIRAM (1u << 10)

#define heap_caps_malloc(bytes, caps) malloc(bytes)
#define heap_caps_calloc(n, bytes, caps) calloc((n), (bytes))
#define heap_caps_free(ptr) free(ptr)
#define heap_caps_aligned_alloc(align, bytes, caps) aligned_alloc((align), (bytes))
