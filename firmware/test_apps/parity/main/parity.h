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

/** Report "block case tensor max_abs=… snr_db=…" for got against want, n floats each, through test_report.
 *  @ctx task | blocking on the console | the caller has begun the PARITY report
 */
void parity_report(const char *block, const char *case_name, const char *tensor, const float *want,
                   const float *got, size_t n);

/** Read every root/block/(*.gold) into buf through read and run it; reports "error" for a case that fails.
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

/** Run one pitch case with the configuration it carries, one hop at a time, resetting where it says; compare
 * the features and the newest frame's NCCF and F0 after every hop.
 *  @ctx task | blocking
 *  @ret false when the case lacks a tensor, has the wrong shape, carries a bad configuration or memory runs
 * out
 */
bool parity_pitch(const char *case_name, const void *buf, size_t len);

/** Run one chain case through a fresh dsp_afe with the settings and calib/bal of the case, if it has one,
 *  resetting where it says; compare every frame field. The build's modules decide which golden set holds.
 *  @ctx task | blocking | each case builds a new fft plan, so dl_fft allocates its tables again
 *  @ret false when the case lacks a tensor, has the wrong shape or the facade refuses it
 */
bool parity_chain(const char *case_name, const void *buf, size_t len);

/** parity_chain for contracts/golden/chain_modules, reported under that block.
 *  @ctx task | blocking | the build must turn on exactly the modules of firmware/sdkconfig.afe
 */
bool parity_chain_modules(const char *case_name, const void *buf, size_t len);

/** Run one hpf case at the cutoff it carries, one hop per call on each channel, as dsp_afe does.
 *  @ctx task | blocking | builds only with CONFIG_DSP_AFE_HPF_ENABLE, where hpf.c is compiled
 *  @ret false when the case lacks a tensor, has the wrong shape or the module refuses it
 */
bool parity_hpf(const char *case_name, const void *buf, size_t len);

/** Run one balance case: every hop of its ch1 bins times its gains, against its output.
 *  @ctx task | blocking | builds only with CONFIG_DSP_AFE_BALANCE_ENABLE, where balance.c is compiled
 *  @ret false when the case lacks a tensor, has the wrong shape or memory runs out
 */
bool parity_balance(const char *case_name, const void *buf, size_t len);

/** Run one vad case with the configuration it carries, one int16 hop / 32768 at a time; compare levels and
 * decisions.
 *  @ctx task | blocking | builds only with CONFIG_DSP_AFE_VAD_ENABLE, where vad.c is compiled
 *  @ret false when the case lacks a tensor, has the wrong shape or the module refuses it
 */
bool parity_vad(const char *case_name, const void *buf, size_t len);

/** Run one doa case with the configuration it carries: both channels' bins one hop at a time, searching where
 * the case says; compare the angle and confidence after every hop.
 *  @ctx task | blocking | builds only with CONFIG_DSP_AFE_DOA_ENABLE, where doa.c is compiled
 *  @ret false when the case lacks a tensor, has the wrong shape or the module refuses it
 */
bool parity_doa(const char *case_name, const void *buf, size_t len);

/** Run one gsc case with the configuration it carries: both channels' bins one hop at a time, steered and
 * learning where the case says; compare the output bins.
 *  @ctx task | blocking | builds only with CONFIG_DSP_AFE_GSC_ENABLE, where gsc.c is compiled
 *  @ret false when the case lacks a tensor, has the wrong shape or the module refuses it
 */
bool parity_gsc(const char *case_name, const void *buf, size_t len);

/** Run one ns_omlsa case with the gain floor it carries, one hop of power at a time with its residual echo,
 * if any; compare the gains and the speech probability.
 *  @ctx task | blocking | builds only with CONFIG_DSP_AFE_NS_OMLSA_ENABLE, where ns_omlsa.c is compiled
 *  @ret false when the case lacks a tensor, has the wrong shape or the module refuses it
 */
bool parity_ns_omlsa(const char *case_name, const void *buf, size_t len);

/** Run one agc case with the target it carries, its int16 hops / 32768 and speech flags; compare the kept
 * output samples and the gain of each hop.
 *  @ctx task | blocking | builds only with CONFIG_DSP_AFE_AGC_ENABLE, where agc.c is compiled
 *  @ret false when the case lacks a tensor, has the wrong shape or the module refuses it
 */
bool parity_agc(const char *case_name, const void *buf, size_t len);

/** Run one g2p case: every item in each dialect with room for as many units as the case has columns; compare
 * the status and the units padded with 0xFF.
 *  @ctx any | non-blocking
 *  @ret false when the case lacks a tensor, has the wrong shape or memory runs out
 */
bool parity_g2p(const char *case_name, const void *buf, size_t len);

/** Run one normalize case into as many bytes as the case has columns; compare the status and the output.
 *  @ctx any | non-blocking
 *  @ret false when the case lacks a tensor, has the wrong shape or memory runs out
 */
bool parity_normalize(const char *case_name, const void *buf, size_t len);

/** Run one lexicon case with the mask of each line; compare the status and every field of lang_vi_pron_t.
 *  @ctx any | non-blocking
 *  @ret false when the case lacks a tensor, has the wrong shape or memory runs out
 */
bool parity_lexicon(const char *case_name, const void *buf, size_t len);
