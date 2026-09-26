/** What leaves the board for the host: the audio stream of KEHOACH 7.4, later telemetry and events.
 *  The stream half exists only with NET_STREAM_ENABLE; sach_task pushes, luong_task sends.
 */
#pragma once

#include <stdint.h>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/stream_buffer.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    StreamBufferHandle_t sb;   // sb_stream: sach_task writes, luong_task reads
    EventGroupHandle_t system; // eg_system; APP_BIT_STREAM_ON mirrors an open stream
    uint8_t raw_channels;      // drv_audio_channels(): 2, or 3 with the speaker reference
} svc_report_stream_config_t;

/** Take the handles the stream half works on.
 *  @ctx task | non-blocking | once at boot, ahead of sach_task and luong_task
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_INVALID_STATE on a second call
 */
esp_err_t svc_report_stream_init(const svc_report_stream_config_t *cfg);

/** Open, or open again with a new deadline, a stream of mode to host:port for duration_s (0: the maximum).
 *  @ctx any task on core 0 | non-blocking | host is copied
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_NOT_SUPPORTED mode needs the reference and there is none
 */
esp_err_t svc_report_stream_start(uint8_t mode, const char *host, uint16_t port, uint32_t duration_s);

/** Close the stream; frames already queued are dropped.
 *  @ctx any | non-blocking
 */
void svc_report_stream_stop(void);

/** Queue one frame of the open mode built from this hop, or count it dropped when sb_stream lacks room.
 *  @ctx sach_task | non-blocking, never waits | raw holds raw_channels interleaved; clean may be NULL
 */
void svc_report_stream_push(uint32_t seq, const int16_t *raw, const int16_t *clean);

/** One turn of luong_task: connect when needed, send one queued frame, close when the stream is off.
 *  @ctx luong_task | blocking up to the connect or send timeout of net_stream
 */
void svc_report_luong_step(void);

/** Frames that found no room in sb_stream since boot, for heartbeat streamDropped.
 *  @ctx any | non-blocking
 */
uint32_t svc_report_stream_dropped(void);

#ifdef __cplusplus
}
#endif
