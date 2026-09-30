/** The variant runners of espsr_compare: each takes one mono or two-channel signal and writes one mono output
 *  of the same length, with its cost in the result (KEHOACH 3.16).
 *  @ctx task | blocking for as long as the item lasts | caller owns every buffer
 */
#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"
#include "job_format.h"

typedef struct {
    int64_t start_us;
    uint64_t idle_us; // both idle tasks' run time at the start
    int64_t peak_us;  // longest single call
    uint32_t calls;
    size_t internal_free; // free heap as the variant starts building
    size_t psram_free;
} espsr_cost_t;

/** Take the free heap as the variant starts building.
 *  @ctx task | non-blocking
 */
void espsr_cost_begin(espsr_cost_t *cost);

/** Start the clock once the variant is built: CPU time of every task but the idle ones from here.
 *  @ctx task | non-blocking
 */
void espsr_cost_start(espsr_cost_t *cost);

/** Record one call that took took_us.
 *  @ctx task | non-blocking
 */
void espsr_cost_call(espsr_cost_t *cost, int64_t took_us);

/** Heap the variant holds now; call while its instance still lives.
 *  @ctx task | non-blocking
 */
void espsr_cost_memory(const espsr_cost_t *cost, espsr_job_result_t *r);

/** Close the count over samples of audio, call_samples a call, into r's per-hop figures.
 *  @ctx task | non-blocking
 */
void espsr_cost_end(espsr_cost_t *cost, size_t samples, size_t call_samples, espsr_job_result_t *r);

/** Map ESP-SR's model partition once and report every model it lists.
 *  @ctx task | blocking, maps flash
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND no model partition or no model in it
 */
esp_err_t espsr_models_load(void);

/** dsp_afe with the variant's spatial path and ns, balance from the job, agc not built.
 *  @ret ESP_OK | ESP_ERR_NO_MEM | whatever dsp_afe_init returns
 */
esp_err_t espsr_run_dsp_afe(const espsr_job_t *job, const espsr_job_variant_t *v, const int16_t *input,
                            int16_t *out, espsr_job_result_t *r);

/** ESP-SR's speech-recognition AFE on both microphones with SE (BSS) alone.
 *  @ret ESP_OK | ESP_ERR_NO_MEM | ESP_ERR_NOT_SUPPORTED a feed format other than two channels at 16 kHz
 */
esp_err_t espsr_run_bss(const espsr_job_t *job, const int16_t *input, int16_t *out, espsr_job_result_t *r);

/** ESP-SR's WebRTC noise suppressor (ns_pro_create) at the variant's level on mono samples.
 *  @ret ESP_OK | ESP_ERR_NO_MEM | ESP_ERR_INVALID_ARG a level outside 0..2
 */
esp_err_t espsr_run_webrtc(const espsr_job_variant_t *v, const int16_t *mono, size_t samples, int16_t *out,
                           espsr_job_result_t *r);

/** ESP-SR's net noise suppressor named by the variant's model on mono samples; the models come from the model
 *  partition packed by items.py.
 *  @ret ESP_OK | ESP_ERR_NOT_FOUND no such model in the partition | ESPSR_JOB_REFUSED the library has no
 * handle for it, or it runs at a rate other than 16 kHz
 */
esp_err_t espsr_run_nsnet(const espsr_job_variant_t *v, const int16_t *mono, size_t samples, int16_t *out,
                          espsr_job_result_t *r);
