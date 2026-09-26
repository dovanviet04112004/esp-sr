#include <dirent.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "dsp_spec.h"
#include "gen_grid.h"
#include "parity.h"

#define MIN_ROUND_TRIP_SNR_DB 110.0
#define HOPS 64
#define MEL_BANDS 40
#define N_CEPS 13
#define PATH_BYTES 512
#define GOLD_SUFFIX ".gold"

typedef bool (*parity_runner_t)(const char *case_name, const void *buf, size_t len);

static const struct {
    const char *block;
    parity_runner_t run;
} kBlocks[] = {
    {"stft", parity_stft},
    {"mel", parity_mel},
};

static unsigned s_failures;
static uint32_t s_lcg = 20260926u;

static void check(bool ok, const char *what)
{
    printf("HOST %s: %s\n", ok ? "PASS" : "FAIL", what);
    s_failures += ok ? 0u : 1u;
}

static float noise(void)
{
    s_lcg = s_lcg * 1664525u + 1013904223u;
    return (float)(s_lcg >> 8) / 16777216.0f - 0.5f;
}

static dsp_spec_fft_t *make_fft(size_t n)
{
    const size_t bytes = dsp_spec_fft_workspace_bytes(n);
    dsp_spec_fft_t *fft = NULL;
    return dsp_spec_fft_init(&fft, n, malloc(bytes), bytes) == ESP_OK ? fft : NULL;
}

static void check_fft(void)
{
    const size_t n = GEN_GRID_FFT_SIZE;
    static float in[GEN_GRID_FFT_SIZE], back[GEN_GRID_FFT_SIZE];
    static dsp_spec_cplx_t bins[GEN_GRID_N_BINS];
    dsp_spec_fft_t *fft = make_fft(n);
    check(fft != NULL, "a 512-point FFT builds");
    if (fft == NULL) { return; }
    for (size_t i = 0; i < n; i++) {
        in[i] = noise();
    }
    dsp_spec_fft_forward(fft, in, bins);
    double worst = 0.0, peak = 0.0;
    for (size_t k = 0; k <= n / 2; k++) {
        double re = 0.0, im = 0.0;
        for (size_t t = 0; t < n; t++) {
            const double a = -2.0 * M_PI * (double)(k * t % n) / (double)n;
            re += in[t] * cos(a);
            im += in[t] * sin(a);
        }
        worst = fmax(worst, hypot(bins[k].re - re, bins[k].im - im));
        peak = fmax(peak, hypot(re, im));
    }
    check(worst / peak < 1e-5, "forward matches a double-precision DFT");
    bins[0].im = 5.0f;
    bins[n / 2].im = -3.0f;
    dsp_spec_fft_inverse(fft, bins, back);
    float err = 0.0f;
    for (size_t i = 0; i < n; i++) {
        err = fmaxf(err, fabsf(back[i] - in[i]));
    }
    check(err < 1e-6f, "inverse undoes forward and ignores DC and Nyquist imaginary parts");
    check(dsp_spec_fft_workspace_bytes(100) == 0 && dsp_spec_fft_workspace_bytes(4096) == 0,
          "lengths the contract refuses are refused");
}

static void check_round_trip(void)
{
    static float signal[HOPS * GEN_GRID_HOP_SAMPLES], rebuilt[HOPS * GEN_GRID_HOP_SAMPLES];
    static dsp_spec_cplx_t bins[GEN_GRID_N_BINS];
    dsp_spec_fft_t *fft = make_fft(GEN_GRID_FFT_SIZE);
    dsp_spec_stft_t *st = NULL;
    dsp_spec_istft_t *ist = NULL;
    const bool built = fft != NULL &&
                       dsp_spec_stft_init(&st, fft, malloc(dsp_spec_stft_workspace_bytes()),
                                          dsp_spec_stft_workspace_bytes()) == ESP_OK &&
                       dsp_spec_istft_init(&ist, fft, malloc(dsp_spec_istft_workspace_bytes()),
                                           dsp_spec_istft_workspace_bytes()) == ESP_OK;
    check(built, "the STFT pair builds");
    if (!built) { return; }
    for (size_t i = 0; i < HOPS * GEN_GRID_HOP_SAMPLES; i++) {
        signal[i] = noise();
    }
    for (size_t h = 0; h < HOPS; h++) {
        dsp_spec_stft_analyze(st, signal + h * GEN_GRID_HOP_SAMPLES, bins);
        dsp_spec_istft_synthesize(ist, bins, rebuilt + h * GEN_GRID_HOP_SAMPLES);
    }
    double power = 0.0, error = 0.0;
    for (size_t i = GEN_GRID_HOP_SAMPLES; i < HOPS * GEN_GRID_HOP_SAMPLES; i++) {
        const double d = (double)rebuilt[i] - signal[i - GEN_GRID_HOP_SAMPLES];
        power += (double)signal[i - GEN_GRID_HOP_SAMPLES] * signal[i - GEN_GRID_HOP_SAMPLES];
        error += d * d;
    }
    check(10.0 * log10(power / fmax(error, 1e-300)) > MIN_ROUND_TRIP_SNR_DB,
          "analysis then synthesis returns the input one hop late");
}

static void check_mel(void)
{
    const dsp_spec_mel_config_t cfg = {MEL_BANDS, 20.0f, 7600.0f, 1e-6f};
    static dsp_spec_cplx_t silence[GEN_GRID_N_BINS];
    float log_mel[MEL_BANDS], ceps[N_CEPS];
    dsp_spec_mel_t *bank = NULL;
    const size_t bytes = dsp_spec_mel_workspace_bytes(&cfg);
    check(dsp_spec_mel_init(&bank, &cfg, malloc(bytes), bytes) == ESP_OK, "a 40-band filterbank builds");
    if (bank == NULL) { return; }
    dsp_spec_mel_log(bank, silence, log_mel);
    bool floor_ok = true;
    for (size_t b = 0; b < MEL_BANDS; b++) {
        floor_ok = floor_ok && fabsf(log_mel[b] - logf(cfg.log_floor)) < 1e-5f;
    }
    check(floor_ok, "log-mel of silence is the log floor");
    for (size_t b = 0; b < MEL_BANDS; b++) {
        log_mel[b] = noise();
    }
    dsp_spec_mel_mfcc(bank, log_mel, ceps, N_CEPS);
    bool dct_ok = true;
    for (size_t k = 0; k < N_CEPS; k++) {
        double want = 0.0;
        for (size_t i = 0; i < MEL_BANDS; i++) {
            want += log_mel[i] * cos(M_PI * (double)k * (2.0 * i + 1.0) / (2.0 * MEL_BANDS));
        }
        want *= sqrt((k == 0 ? 1.0 : 2.0) / MEL_BANDS);
        dct_ok = dct_ok && fabs(ceps[k] - want) < 1e-5;
    }
    check(dct_ok, "MFCC is the orthonormal DCT-II");
}

static void *read_file(const char *path, size_t *len)
{
    FILE *file = fopen(path, "rb");
    if (file == NULL) { return NULL; }
    fseek(file, 0, SEEK_END);
    const long size = ftell(file);
    rewind(file);
    void *buf = size > 0 ? malloc((size_t)size) : NULL;
    *len = buf != NULL ? fread(buf, 1, (size_t)size, file) : 0;
    fclose(file);
    return buf;
}

static unsigned run_golden(const char *root)
{
    unsigned cases = 0;
    for (size_t b = 0; b < sizeof(kBlocks) / sizeof(kBlocks[0]); b++) {
        char dir_path[PATH_BYTES];
        snprintf(dir_path, sizeof(dir_path), "%s/%s", root, kBlocks[b].block);
        DIR *dir = opendir(dir_path);
        if (dir == NULL) {
            printf("PARITY missing %s\n", dir_path);
            continue;
        }
        for (struct dirent *entry = readdir(dir); entry != NULL; entry = readdir(dir)) {
            const size_t name_len = strlen(entry->d_name);
            const size_t suffix_len = strlen(GOLD_SUFFIX);
            if (name_len <= suffix_len || strcmp(entry->d_name + name_len - suffix_len, GOLD_SUFFIX) != 0) {
                continue;
            }
            char path[2 * PATH_BYTES];
            char case_name[PATH_BYTES];
            snprintf(path, sizeof(path), "%s/%s", dir_path, entry->d_name);
            snprintf(case_name, sizeof(case_name), "%.*s", (int)(name_len - suffix_len), entry->d_name);
            size_t len = 0;
            void *buf = read_file(path, &len);
            if (buf == NULL || !kBlocks[b].run(case_name, buf, len)) {
                printf("PARITY error %s %s\n", kBlocks[b].block, case_name);
                s_failures++;
            } else {
                cases++;
            }
            free(buf);
        }
        closedir(dir);
    }
    return cases;
}

int main(int argc, char **argv)
{
    check_fft();
    check_round_trip();
    check_mel();
    if (argc > 1) { printf("PARITY done %u cases\n", run_golden(argv[1])); }
    printf("HOST %u failure(s)\n", s_failures);
    return s_failures == 0 ? 0 : 1;
}
