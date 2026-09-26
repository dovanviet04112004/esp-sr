/** The parity app: every case of contracts/golden run through the C blocks, errors printed for the host.
 *  @ctx task | blocking | the host judges each PARITY line against tolerance.yaml (KEHOACH 3.14)
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>

#include "gold_read.h"

/** Find the tensor called name in a .gold buffer.
 *  @ctx any | non-blocking | out points into buf
 */
bool parity_tensor(const void *buf, size_t len, const char *name, gold_tensor_t *out);

/** Print "PARITY block case tensor max_abs=… snr_db=…" for got against want, n floats each.
 *  @ctx task | blocking on the console
 */
void parity_report(const char *block, const char *case_name, const char *tensor, const float *want,
                   const float *got, size_t n);

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
