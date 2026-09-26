/** The parity app: every case of contracts/golden run through the C blocks, errors printed for the host.
 *  @ctx task | blocking | the host judges each PARITY line against tolerance.yaml (KEHOACH 3.14)
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "gold_read.h"

typedef bool (*parity_runner_t)(const char *case_name, const void *buf, size_t len);
typedef bool (*parity_reader_t)(const char *path, void *buf, size_t cap, size_t *len);

/** Find the tensor called name in a .gold buffer.
 *  @ctx any | non-blocking | out points into buf
 */
bool parity_tensor(const void *buf, size_t len, const char *name, gold_tensor_t *out);

/** Copy the n values of a tensor of any .gold dtype into out as floats.
 *  @ctx any | non-blocking
 *  @ret false when the tensor does not hold exactly n values
 */
bool parity_floats(const gold_tensor_t *t, float *out, size_t n);

/** Print "PARITY block case tensor max_abs=… snr_db=…" for got against want, n floats each.
 *  @ctx task | blocking on the console
 */
void parity_report(const char *block, const char *case_name, const char *tensor, const float *want,
                   const float *got, size_t n);

/** Read every root/block/(*.gold) into buf through read and run it; "PARITY error" for a case that fails.
 *  @ctx task | blocking on the filesystem and the console
 *  @ret cases run; errors, when not NULL, gets the cases that could not run
 */
unsigned parity_run_block(const char *root, const char *block, parity_runner_t run, parity_reader_t read,
                          void *buf, size_t cap, unsigned *errors);

/** Run one STFT case: analysis against its bins, synthesis of its bins against its rebuilt signal.
 *  @ctx task | blocking
 *  @ret false when the case lacks a tensor or memory runs out
 */
bool parity_stft(const char *case_name, const void *buf, size_t len);

/** Run one mel case with the configuration it carries: log-mel of its bins, then MFCC of that log-mel.
 *  @ctx task | blocking
 *  @ret false when the case lacks a tensor, carries a bad configuration or memory runs out
 */
bool parity_mel(const char *case_name, const void *buf, size_t len);

/** Run one chain case through a fresh default dsp_afe, resetting where it says; compare every frame field.
 *  @ctx task | blocking | each case builds a new fft plan, so dl_fft allocates its tables again
 *  @ret false when the case lacks a tensor, has the wrong shape or the facade refuses it
 */
bool parity_chain(const char *case_name, const void *buf, size_t len);

/** Run one hpf case at the cutoff it carries, one hop per call on each channel, as dsp_afe does.
 *  @ctx task | blocking | builds only with CONFIG_DSP_AFE_HPF_ENABLE, where hpf.c is compiled
 *  @ret false when the case lacks a tensor, has the wrong shape or the module refuses it
 */
bool parity_hpf(const char *case_name, const void *buf, size_t len);
