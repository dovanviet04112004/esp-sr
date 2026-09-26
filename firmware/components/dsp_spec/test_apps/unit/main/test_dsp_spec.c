#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dl_rfft.h"
#include "dsp_spec.h"
#include "dsps_fft2r.h"
#include "esp_heap_caps.h"
#include "esp_timer.h"
#include "gen_grid.h"
#include "sdkconfig.h"
#include "unity.h"

#if CONFIG_DSP_SPEC_FFT_ESP_DSP
#define BACKEND "esp-dsp"
#else
#define BACKEND "dl_fft"
#endif

#define MAX_POINTS 2048
#define HOPS 64
#define BENCH_RUNS 1000
#define MIN_ROUND_TRIP_SNR_DB 110.0
#define MEL_BANDS 40
#define MEL_F_MIN_HZ 20.0f
#define MEL_F_MAX_HZ 7600.0f
#define MEL_LOG_FLOOR 1e-6f
#define N_CEPS 13

static float s_in[MAX_POINTS];
static float s_out[MAX_POINTS];
static dsp_spec_cplx_t s_bins[MAX_POINTS / 2 + 1];
static float s_signal[HOPS * GEN_GRID_HOP_SAMPLES];
static float s_rebuilt[HOPS * GEN_GRID_HOP_SAMPLES];
static uint32_t s_lcg = 20260926u;

static float noise(void)
{
    s_lcg = s_lcg * 1664525u + 1013904223u;
    return (float)(s_lcg >> 8) / 16777216.0f - 0.5f;
}

static dsp_spec_fft_t *make_fft(size_t n, size_t *table_bytes)
{
    const size_t bytes = dsp_spec_fft_workspace_bytes(n);
    void *mem = heap_caps_malloc(bytes, MALLOC_CAP_INTERNAL);
    TEST_ASSERT_NOT_NULL(mem);
    const size_t before = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    dsp_spec_fft_t *fft = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, dsp_spec_fft_init(&fft, n, mem, bytes));
    if (table_bytes != NULL) { *table_bytes = before - heap_caps_get_free_size(MALLOC_CAP_INTERNAL); }
    return fft;
}

TEST_CASE("forward matches a double-precision DFT", "[dsp_spec]")
{
    const size_t n = GEN_GRID_FFT_SIZE;
    size_t table_bytes = 0;
    dsp_spec_fft_t *fft = make_fft(n, &table_bytes);
    printf("MEASURE %s f32  512: library tables %u B at the first init\n", BACKEND, (unsigned)table_bytes);
    for (size_t i = 0; i < n; i++) {
        s_in[i] = noise();
    }
    TEST_ASSERT_EQUAL(ESP_OK, dsp_spec_fft_forward(fft, s_in, s_bins));
    double worst = 0.0, peak = 0.0;
    for (size_t k = 0; k <= n / 2; k++) {
        double re = 0.0, im = 0.0;
        for (size_t t = 0; t < n; t++) {
            const double a = -2.0 * M_PI * (double)(k * t % n) / (double)n;
            re += s_in[t] * cos(a);
            im += s_in[t] * sin(a);
        }
        worst = fmax(worst, hypot(s_bins[k].re - re, s_bins[k].im - im));
        peak = fmax(peak, hypot(re, im));
    }
    printf("MEASURE %s forward vs DFT: worst %.3e of peak %.3e\n", BACKEND, worst, peak);
    TEST_ASSERT_TRUE(worst / peak < 1e-5);
}

TEST_CASE("inverse undoes forward and ignores DC and Nyquist imaginary parts", "[dsp_spec]")
{
    const size_t n = GEN_GRID_FFT_SIZE;
    dsp_spec_fft_t *fft = make_fft(n, NULL);
    for (size_t i = 0; i < n; i++) {
        s_in[i] = noise();
    }
    TEST_ASSERT_EQUAL(ESP_OK, dsp_spec_fft_forward(fft, s_in, s_bins));
    s_bins[0].im = 5.0f;
    s_bins[n / 2].im = -3.0f;
    TEST_ASSERT_EQUAL(ESP_OK, dsp_spec_fft_inverse(fft, s_bins, s_out));
    float worst = 0.0f;
    for (size_t i = 0; i < n; i++) {
        worst = fmaxf(worst, fabsf(s_out[i] - s_in[i]));
    }
    printf("MEASURE %s inverse(forward): worst %.3e\n", BACKEND, worst);
    TEST_ASSERT_TRUE(worst < 1e-6f);
}

TEST_CASE("lengths and memory the contract refuses are refused", "[dsp_spec]")
{
    uint8_t small[32];
    dsp_spec_fft_t *fft = NULL;
    TEST_ASSERT_EQUAL(0, dsp_spec_fft_workspace_bytes(100));
    TEST_ASSERT_EQUAL(0, dsp_spec_fft_workspace_bytes(4096));
    TEST_ASSERT_EQUAL(ESP_ERR_INVALID_SIZE, dsp_spec_fft_init(&fft, GEN_GRID_FFT_SIZE, small, sizeof(small)));
}

TEST_CASE("analysis then synthesis returns the input one hop late", "[dsp_spec]")
{
    dsp_spec_fft_t *fft = make_fft(GEN_GRID_FFT_SIZE, NULL);
    void *st_mem = malloc(dsp_spec_stft_workspace_bytes());
    void *is_mem = malloc(dsp_spec_istft_workspace_bytes());
    dsp_spec_stft_t *st = NULL;
    dsp_spec_istft_t *ist = NULL;
    TEST_ASSERT_EQUAL(ESP_OK, dsp_spec_stft_init(&st, fft, st_mem, dsp_spec_stft_workspace_bytes()));
    TEST_ASSERT_EQUAL(ESP_OK, dsp_spec_istft_init(&ist, fft, is_mem, dsp_spec_istft_workspace_bytes()));
    for (size_t i = 0; i < HOPS * GEN_GRID_HOP_SAMPLES; i++) {
        s_signal[i] = noise();
    }
    for (size_t h = 0; h < HOPS; h++) {
        TEST_ASSERT_EQUAL(ESP_OK, dsp_spec_stft_analyze(st, s_signal + h * GEN_GRID_HOP_SAMPLES, s_bins));
        TEST_ASSERT_EQUAL(ESP_OK,
                          dsp_spec_istft_synthesize(ist, s_bins, s_rebuilt + h * GEN_GRID_HOP_SAMPLES));
    }
    double signal = 0.0, error = 0.0;
    for (size_t i = GEN_GRID_HOP_SAMPLES; i < HOPS * GEN_GRID_HOP_SAMPLES; i++) {
        const double d = (double)s_rebuilt[i] - s_signal[i - GEN_GRID_HOP_SAMPLES];
        signal += (double)s_signal[i - GEN_GRID_HOP_SAMPLES] * s_signal[i - GEN_GRID_HOP_SAMPLES];
        error += d * d;
    }
    const double snr = 10.0 * log10(signal / fmax(error, 1e-300));
    printf("MEASURE %s stft round trip: %.1f dB\n", BACKEND, snr);
    TEST_ASSERT_TRUE(snr > MIN_ROUND_TRIP_SNR_DB);
    free(st_mem);
    free(is_mem);
}

TEST_CASE("log-mel of silence is the log floor and MFCC is the orthonormal DCT-II", "[dsp_spec]")
{
    const dsp_spec_mel_config_t cfg = {MEL_BANDS, MEL_F_MIN_HZ, MEL_F_MAX_HZ, MEL_LOG_FLOOR};
    void *mem = malloc(dsp_spec_mel_workspace_bytes(&cfg));
    dsp_spec_mel_t *mel = NULL;
    float log_mel[MEL_BANDS], ceps[N_CEPS];
    TEST_ASSERT_EQUAL(ESP_OK, dsp_spec_mel_init(&mel, &cfg, mem, dsp_spec_mel_workspace_bytes(&cfg)));
    memset(s_bins, 0, sizeof(s_bins));
    TEST_ASSERT_EQUAL(ESP_OK, dsp_spec_mel_log(mel, s_bins, log_mel));
    for (size_t b = 0; b < MEL_BANDS; b++) {
        TEST_ASSERT_FLOAT_WITHIN(1e-5f, logf(MEL_LOG_FLOOR), log_mel[b]);
    }
    for (size_t b = 0; b < MEL_BANDS; b++) {
        log_mel[b] = noise();
    }
    TEST_ASSERT_EQUAL(ESP_OK, dsp_spec_mel_mfcc(mel, log_mel, ceps, N_CEPS));
    for (size_t k = 0; k < N_CEPS; k++) {
        double want = 0.0;
        for (size_t i = 0; i < MEL_BANDS; i++) {
            want += log_mel[i] * cos(M_PI * (double)k * (2.0 * i + 1.0) / (2.0 * MEL_BANDS));
        }
        want *= sqrt((k == 0 ? 1.0 : 2.0) / MEL_BANDS);
        TEST_ASSERT_FLOAT_WITHIN(1e-5f, (float)want, ceps[k]);
    }
    free(mem);
}

static double micros_per_call(esp_err_t (*run)(void *), void *arg)
{
    run(arg);
    const int64_t t0 = esp_timer_get_time();
    for (int i = 0; i < BENCH_RUNS; i++) {
        run(arg);
    }
    return (double)(esp_timer_get_time() - t0) / BENCH_RUNS;
}

static esp_err_t run_forward(void *fft)
{
    return dsp_spec_fft_forward(fft, s_in, s_bins);
}

static esp_err_t run_inverse(void *fft)
{
    return dsp_spec_fft_inverse(fft, s_bins, s_out);
}

TEST_CASE("time the float32 real transforms at 256, 512 and 1024 points", "[dsp_spec][bench]")
{
    static const size_t sizes[] = {256, 512, 1024};
    for (size_t s = 0; s < sizeof(sizes) / sizeof(sizes[0]); s++) {
        size_t table_bytes = 0;
        dsp_spec_fft_t *fft = make_fft(sizes[s], &table_bytes);
        for (size_t i = 0; i < sizes[s]; i++) {
            s_in[i] = noise();
        }
        const double fwd = micros_per_call(run_forward, fft);
        const double inv = micros_per_call(run_inverse, fft);
        printf(
            "MEASURE %s f32 %4u: forward %.1f us, inverse %.1f us, workspace %u B, new library tables %u B\n",
            BACKEND, (unsigned)sizes[s], fwd, inv, (unsigned)dsp_spec_fft_workspace_bytes(sizes[s]),
            (unsigned)table_bytes);
    }
}

static struct {
    dsp_spec_stft_t *st;
    dsp_spec_istft_t *ist;
    dsp_spec_mel_t *mel;
    float log_mel[MEL_BANDS];
} s_chain;

static esp_err_t run_analyze(void *arg)
{
    return dsp_spec_stft_analyze(s_chain.st, s_in, s_bins);
}

static esp_err_t run_synthesize(void *arg)
{
    return dsp_spec_istft_synthesize(s_chain.ist, s_bins, s_out);
}

static esp_err_t run_mel(void *arg)
{
    return dsp_spec_mel_log(s_chain.mel, s_bins, s_chain.log_mel);
}

TEST_CASE("time one hop of analysis, synthesis and log-mel", "[dsp_spec][bench]")
{
    const dsp_spec_mel_config_t cfg = {MEL_BANDS, MEL_F_MIN_HZ, MEL_F_MAX_HZ, MEL_LOG_FLOOR};
    dsp_spec_fft_t *fft = make_fft(GEN_GRID_FFT_SIZE, NULL);
    TEST_ASSERT_EQUAL(ESP_OK, dsp_spec_stft_init(&s_chain.st, fft, malloc(dsp_spec_stft_workspace_bytes()),
                                                 dsp_spec_stft_workspace_bytes()));
    TEST_ASSERT_EQUAL(ESP_OK, dsp_spec_istft_init(&s_chain.ist, fft, malloc(dsp_spec_istft_workspace_bytes()),
                                                  dsp_spec_istft_workspace_bytes()));
    TEST_ASSERT_EQUAL(ESP_OK,
                      dsp_spec_mel_init(&s_chain.mel, &cfg, malloc(dsp_spec_mel_workspace_bytes(&cfg)),
                                        dsp_spec_mel_workspace_bytes(&cfg)));
    for (size_t i = 0; i < GEN_GRID_HOP_SAMPLES; i++) {
        s_in[i] = noise();
    }
    printf("MEASURE %s per hop: stft %.1f us, istft %.1f us, log-mel %d bands %.1f us; workspaces stft %u, "
           "istft "
           "%u, mel %u B\n",
           BACKEND, micros_per_call(run_analyze, NULL), micros_per_call(run_synthesize, NULL), MEL_BANDS,
           micros_per_call(run_mel, NULL), (unsigned)dsp_spec_stft_workspace_bytes(),
           (unsigned)dsp_spec_istft_workspace_bytes(), (unsigned)dsp_spec_mel_workspace_bytes(&cfg));
}

static int16_t s_q15[MAX_POINTS] __attribute__((aligned(16)));

TEST_CASE("time the int16 transforms of both libraries at 512 points", "[dsp_spec][bench]")
{
    const int n = GEN_GRID_FFT_SIZE;
    dl_fft_s16_t *plan = dl_rfft_s16_init(n, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    TEST_ASSERT_NOT_NULL(plan);
    TEST_ASSERT_EQUAL(ESP_OK, dsps_fft2r_init_sc16(NULL, MAX_POINTS / 2));
    int exponent = 0;
    int64_t t0 = esp_timer_get_time();
    for (int i = 0; i < BENCH_RUNS; i++) {
        for (int k = 0; k < n; k++) {
            s_q15[k] = (int16_t)(noise() * 16384.0f);
        }
        dl_rfft_s16_run(plan, s_q15, 0, &exponent);
    }
    const double dl = (double)(esp_timer_get_time() - t0) / BENCH_RUNS;
    t0 = esp_timer_get_time();
    for (int i = 0; i < BENCH_RUNS; i++) {
        for (int k = 0; k < n; k++) {
            s_q15[k] = (int16_t)(noise() * 16384.0f);
        }
        dsps_fft2r_sc16(s_q15, n / 2);
        dsps_bit_rev_sc16_ansi(s_q15, n / 2);
    }
    const double dsp = (double)(esp_timer_get_time() - t0) / BENCH_RUNS;
    t0 = esp_timer_get_time();
    for (int i = 0; i < BENCH_RUNS; i++) {
        for (int k = 0; k < n; k++) {
            s_q15[k] = (int16_t)(noise() * 16384.0f);
        }
    }
    const double fill = (double)(esp_timer_get_time() - t0) / BENCH_RUNS;
    printf("MEASURE int16 512: dl_rfft_s16 %.1f us, esp-dsp complex 256 + bit reversal %.1f us (fill "
           "excluded)\n",
           dl - fill, dsp - fill);
    dl_rfft_s16_deinit(plan);
}

void app_main(void)
{
    UNITY_BEGIN();
    unity_run_all_tests();
    UNITY_END();
}
