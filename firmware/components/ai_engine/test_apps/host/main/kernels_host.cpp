#include <openssl/sha.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>

#include "kernel_cases.h"

namespace {

constexpr size_t kHeaderBytes = 16; // the mapping's size, keeping the buffer 16-byte aligned

// MAP_32BIT keeps every buffer below 2 GB, so the 32-bit slots of a run hold it as on the chip.
void *alloc(size_t bytes)
{
    void *map = mmap(nullptr, bytes + kHeaderBytes, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_32BIT, -1, 0);
    if (map == MAP_FAILED) {
        fprintf(stderr, "kernels_host: no mapping below 2 GB for %zu bytes\n", bytes);
        return nullptr;
    }
    const size_t total = bytes + kHeaderBytes;
    memcpy(map, &total, sizeof(total));
    return static_cast<uint8_t *>(map) + kHeaderBytes;
}

void release(void *buffer)
{
    uint8_t *map = static_cast<uint8_t *>(buffer) - kHeaderBytes;
    size_t total;
    memcpy(&total, map, sizeof(total));
    munmap(map, total);
}

void report(void *, const char *kernel, int index, const void *bytes, size_t n)
{
    unsigned char digest[SHA256_DIGEST_LENGTH];
    SHA256(static_cast<const unsigned char *>(bytes), n, digest);
    printf("kernel %s %d ", kernel, index);
    for (unsigned char b : digest) {
        printf("%02x", b);
    }
    printf("\n");
}

} // namespace

int main()
{
    const KernelSink sink = {alloc, release, report, nullptr};
    kernel_cases_c(&sink);
    return 0;
}
