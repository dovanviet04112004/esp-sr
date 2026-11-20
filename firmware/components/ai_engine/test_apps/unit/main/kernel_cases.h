/** The kernel cases kernel_cases.inc runs, built once per implementation of the kernels (ADR-0045): the same
 *  seeded inputs through every kernel, each output handed to the sink.
 *  @ctx task | blocking | the sink allocates every buffer
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

struct KernelSink {
    void *(*alloc)(size_t bytes); // 16-byte aligned; below 2 GB on the host
    void (*release)(void *buffer);
    void (*report)(void *user, const char *kernel, int index, const void *bytes, size_t n);
    void *user;
};

/** Run every case through the PIE kernels of the S3.
 *  @ctx task | blocking
 */
void kernel_cases_pie(const KernelSink *sink);

/** Run every case through the plain C kernels.
 *  @ctx task | blocking
 */
void kernel_cases_c(const KernelSink *sink);
