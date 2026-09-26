#include "net_stream.h"

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>

#include "lwip/netdb.h"
#include "lwip/sockets.h"

#define NO_SOCKET (-1)
#define PORT_TEXT_BYTES 6
#define MS_PER_S 1000
#define US_PER_MS 1000

static int s_sock = NO_SOCKET;
static net_stream_stats_t s_stats;

static struct timeval to_timeval(uint32_t timeout_ms)
{
    return (struct timeval){.tv_sec = timeout_ms / MS_PER_S, .tv_usec = (timeout_ms % MS_PER_S) * US_PER_MS};
}

void net_stream_close(void)
{
    if (s_sock != NO_SOCKET) {
        close(s_sock);
        s_sock = NO_SOCKET;
    }
}

bool net_stream_is_connected(void)
{
    return s_sock != NO_SOCKET;
}

static esp_err_t finish_connect(int sock, uint32_t timeout_ms)
{
    fd_set writable;
    FD_ZERO(&writable);
    FD_SET(sock, &writable);
    struct timeval tv = to_timeval(timeout_ms);
    const int ready = select(sock + 1, NULL, &writable, NULL, &tv);
    if (ready == 0) { return ESP_ERR_TIMEOUT; }
    int so_error = 0;
    socklen_t len = sizeof(so_error);
    if (ready < 0 || getsockopt(sock, SOL_SOCKET, SO_ERROR, &so_error, &len) != 0 || so_error != 0) {
        return ESP_FAIL;
    }
    return ESP_OK;
}

esp_err_t net_stream_connect(const char *host, uint16_t port, uint32_t timeout_ms)
{
    net_stream_close();
    if (host == NULL || host[0] == '\0' || port == 0) { return ESP_ERR_INVALID_ARG; }
    char port_text[PORT_TEXT_BYTES];
    snprintf(port_text, sizeof(port_text), "%u", (unsigned)port);
    const struct addrinfo hints = {.ai_family = AF_INET, .ai_socktype = SOCK_STREAM};
    struct addrinfo *addr = NULL;
    if (getaddrinfo(host, port_text, &hints, &addr) != 0 || addr == NULL) { return ESP_ERR_NOT_FOUND; }
    const int sock = socket(addr->ai_family, addr->ai_socktype, addr->ai_protocol);
    if (sock < 0) {
        freeaddrinfo(addr);
        return ESP_FAIL;
    }
    // Non-blocking connect so a host that never answers costs timeout_ms, not the lwIP default of minutes.
    fcntl(sock, F_SETFL, fcntl(sock, F_GETFL, 0) | O_NONBLOCK);
    const int rc = connect(sock, addr->ai_addr, addr->ai_addrlen);
    freeaddrinfo(addr);
    esp_err_t err = rc == 0 ? ESP_OK : errno == EINPROGRESS ? finish_connect(sock, timeout_ms) : ESP_FAIL;
    if (err == ESP_OK) {
        fcntl(sock, F_SETFL, fcntl(sock, F_GETFL, 0) & ~O_NONBLOCK);
        const int one = 1;
        setsockopt(sock, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
        s_sock = sock;
        s_stats.connects++;
    } else {
        close(sock);
    }
    return err;
}

esp_err_t net_stream_send(const void *buf, size_t len, uint32_t timeout_ms)
{
    if (s_sock == NO_SOCKET) { return ESP_ERR_INVALID_STATE; }
    const struct timeval tv = to_timeval(timeout_ms);
    setsockopt(s_sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
    const uint8_t *at = buf;
    size_t left = len;
    while (left > 0) {
        const ssize_t sent = send(s_sock, at, left, 0);
        if (sent <= 0) {
            const esp_err_t err = (errno == EAGAIN || errno == EWOULDBLOCK) ? ESP_ERR_TIMEOUT : ESP_FAIL;
            s_stats.send_failures++;
            net_stream_close();
            return err;
        }
        at += sent;
        left -= (size_t)sent;
    }
    s_stats.bytes_sent += (uint32_t)len;
    return ESP_OK;
}

void net_stream_stats(net_stream_stats_t *out)
{
    *out = s_stats;
}
