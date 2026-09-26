/** TCP client of the audio stream: the board connects out to the host, never listens (KEHOACH 7.4).
 *  One connection at a time, driven by luong_task alone; built only with NET_STREAM_ENABLE.
 */
#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint32_t connects;      // connections that came up
    uint32_t send_failures; // frames given up, each closing the connection
    uint32_t bytes_sent;
} net_stream_stats_t;

/** Open a connection to host (name or dotted address) and port, closing any open one first.
 *  @ctx luong_task | blocking up to timeout_ms plus a name lookup
 *  @ret ESP_OK | ESP_ERR_INVALID_ARG | ESP_ERR_NOT_FOUND unknown host | ESP_ERR_TIMEOUT | ESP_FAIL refused
 */
esp_err_t net_stream_connect(const char *host, uint16_t port, uint32_t timeout_ms);

/** Send all len bytes; on any failure the connection is closed so the next call must connect again.
 *  @ctx luong_task | blocking up to timeout_ms
 *  @ret ESP_OK | ESP_ERR_INVALID_STATE not connected | ESP_ERR_TIMEOUT | ESP_FAIL
 */
esp_err_t net_stream_send(const void *buf, size_t len, uint32_t timeout_ms);

/** Close the connection if one is open.
 *  @ctx luong_task | non-blocking
 */
void net_stream_close(void);

/** Whether a connection is open, as far as the last send knows.
 *  @ctx luong_task | non-blocking
 */
bool net_stream_is_connected(void);

/** Copy the counters.
 *  @ctx any | non-blocking | a torn read across tasks is possible and harmless
 */
void net_stream_stats(net_stream_stats_t *out);

#ifdef __cplusplus
}
#endif
