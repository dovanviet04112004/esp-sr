#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "core/espdl/ops/espdl_float.hpp"
#include "core/espdl/ops/espdl_float_ieee.hpp"
#include "esp_heap_caps.h"
#include "kernel_cases.h"
#include "sha/sha_core.h"
#include "test_report.h"

extern "C" void ai_engine_stage_sum_row_s8_esp32s3(int8_t *row, const int8_t *x, const int8_t *weights,
                                                   const int32_t *run);

namespace {

constexpr size_t kMaxCases = 640;
constexpr size_t kReportLines = kMaxCases + 8;
constexpr size_t kDigestBytes = 32;
constexpr size_t kAlignBytes = 16;
constexpr int kNegativeCases = 32;
constexpr int kNegativeVectors = 4;
constexpr size_t kVectorBytes = 16;
constexpr int kFloatCases = 100000;

struct Digest {
    const char *kernel;
    int index;
    uint8_t bytes[kDigestBytes];
};

Digest *s_pie = nullptr;
size_t s_pie_count = 0;
size_t s_c_count = 0;
int s_differ = 0;

void *alloc(size_t bytes)
{
    return heap_caps_aligned_alloc(kAlignBytes, bytes, MALLOC_CAP_SPIRAM);
}

void release(void *buffer)
{
    heap_caps_free(buffer);
}

void hex_of(const uint8_t *digest, char *hex)
{
    for (size_t i = 0; i < kDigestBytes; i++) {
        snprintf(hex + 2 * i, 3, "%02x", digest[i]);
    }
}

void record_pie(void *, const char *kernel, int index, const void *bytes, size_t n)
{
    if (s_pie_count == kMaxCases) { return; }
    Digest &d = s_pie[s_pie_count++];
    d.kernel = kernel;
    d.index = index;
    esp_sha(SHA2_256, static_cast<const unsigned char *>(bytes), n, d.bytes);
}

void check_c(void *, const char *kernel, int index, const void *bytes, size_t n)
{
    uint8_t digest[kDigestBytes];
    esp_sha(SHA2_256, static_cast<const unsigned char *>(bytes), n, digest);
    const Digest *pie = s_c_count < s_pie_count ? &s_pie[s_c_count] : nullptr;
    s_c_count++;
    const bool same = pie != nullptr && strcmp(pie->kernel, kernel) == 0 && pie->index == index &&
                      memcmp(pie->bytes, digest, kDigestBytes) == 0;
    s_differ += same ? 0 : 1;
    char hex[2 * kDigestBytes + 1];
    hex_of(pie != nullptr ? pie->bytes : digest, hex);
    test_report_line("kernel %s %d %s %s", kernel, index, hex, same ? "same" : "differs");
}

// The negative control: round half down where tie728 rounds half up must differ from the PIE kernel.
void stage_sum_half_down(int8_t *row, const int8_t *x, const int8_t *weights, const int32_t *run)
{
    for (int i = 0; i < run[0] * static_cast<int>(kVectorBytes); i++) {
        const int32_t p = static_cast<int32_t>(x[i]) * weights[i];
        const int32_t v = run[1] > 0 ? (p - 1 + (1 << (run[1] - 1))) >> run[1] : p;
        const int8_t product = static_cast<int8_t>(v > INT8_MAX ? INT8_MAX : v < INT8_MIN ? INT8_MIN : v);
        row[i] = product;
    }
}

int negative_control()
{
    const size_t bytes = kNegativeVectors * kVectorBytes;
    auto *x = static_cast<int8_t *>(alloc(bytes));
    auto *w = static_cast<int8_t *>(alloc(bytes));
    auto *a = static_cast<int8_t *>(alloc(bytes));
    auto *b = static_cast<int8_t *>(alloc(bytes));
    uint32_t state = 0x2545f491u;
    int differ = 0;
    for (int k = 0; k < kNegativeCases; k++) {
        for (size_t i = 0; i < bytes; i++) {
            state ^= state << 13;
            state ^= state >> 17;
            state ^= state << 5;
            x[i] = static_cast<int8_t>(state >> 24);
            w[i] = static_cast<int8_t>(state >> 16);
        }
        const int32_t run[] = {kNegativeVectors, 1 + k % 7, 1};
        ai_engine_stage_sum_row_s8_esp32s3(a, x, w, run);
        stage_sum_half_down(b, x, w, run);
        differ += memcmp(a, b, bytes) != 0 ? 1 : 0;
    }
    release(x);
    release(w);
    release(a);
    release(b);
    return differ;
}

uint32_t next(uint32_t &state)
{
    state ^= state << 13;
    state ^= state >> 17;
    state ^= state << 5;
    return state;
}

// A normal float of exponent field 64..190, so every quotient of two stays normal.
float normal(uint32_t &state)
{
    const uint32_t r = next(state);
    const uint32_t bits = (r & 0x807fffffu) | ((64u + (r >> 8) % 127u) << 23);
    float f;
    memcpy(&f, &bits, sizeof(f));
    return f;
}

int divides_apart()
{
    uint32_t state = 0x6b8b4567u;
    int apart = 0;
    for (int i = 0; i < kFloatCases; i++) {
        const float a = normal(state);
        const float b = normal(state);
        const float chip = ai::divide(a, b);
        const float ieee = ai::ieee::divide(a, b);
        apart += memcmp(&chip, &ieee, sizeof(float)) != 0 ? 1 : 0;
    }
    return apart;
}

int roundings_apart()
{
    uint32_t state = 0x327b23c6u;
    int apart = 0;
    for (int i = 0; i < kFloatCases; i++) {
        const float halves = static_cast<float>(static_cast<int32_t>(next(state) % 1201u) - 600) * 0.5f;
        const float v = i % 2 ? halves : halves + static_cast<float>(next(state) >> 8) * (1.0f / 16777216.0f);
        apart += ai::round_half_up(v) != ai::ieee::round_half_up(v) ? 1 : 0;
    }
    return apart;
}

} // namespace

extern "C" void app_main(void)
{
    s_pie = static_cast<Digest *>(heap_caps_calloc(kMaxCases, sizeof(Digest), MALLOC_CAP_SPIRAM));
    if (s_pie == nullptr || !test_report_begin("kernels", kReportLines)) {
        printf("kernels: no memory for the report\n");
        return;
    }
    const KernelSink pie = {alloc, release, record_pie, nullptr};
    kernel_cases_pie(&pie);
    const KernelSink c = {alloc, release, check_c, nullptr};
    kernel_cases_c(&c);
    test_report_line("negative stage_sum_row_s8 %d of %d differ", negative_control(), kNegativeCases);
    test_report_line("float divide %d of %d apart", divides_apart(), kFloatCases);
    test_report_line("float round %d of %d apart", roundings_apart(), kFloatCases);
    test_report_line("summary %u %u %d", static_cast<unsigned>(s_pie_count), static_cast<unsigned>(s_c_count),
                     s_differ);
    test_report_serve();
}
