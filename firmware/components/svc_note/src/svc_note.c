#include "svc_note.h"

#include <inttypes.h>
#include <stdio.h>
#include <string.h>

#include "app_events.h"
#include "bsp_board.h"
#include "drv_sd.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "gen_grid.h"
#include "gen_vision.h"
#include "net_mqtt.h"

#define PATH_BYTES 32
#define HOP_WAIT_MS 100
#define SLACK_S 5.0f // past note.max_s, no hops: nhan_task stopped
#define US_PER_S 1000000
#define US_PER_MS 1000
#define MS_PER_S 1000
#define WAV_HEADER_BYTES 44
#define WAV_FMT_BYTES 16
#define WAV_PCM 1
#define WAV_CHANNELS 1
#define WAV_BITS 16
#define WAV_BYTES_PER_SAMPLE (WAV_BITS / 8)

static const char *TAG = "svc_note";

static svc_note_config_t s_cfg;
static uint8_t *s_block;
static app_note_hop_t *s_hop;
static uint32_t s_start_hops, s_end_hops, s_max_hops;
static char s_path[PATH_BYTES];
static uint32_t s_seq;
static uint32_t s_saved_bytes; // of the WAV at s_path, 0 unless saved whole

typedef struct {
    uint32_t hops;
    uint32_t dropped;
    uint32_t pcm_bytes;
} taken_t;

esp_err_t svc_note_init(const svc_note_config_t *cfg)
{
    if (cfg == NULL || cfg->hops == NULL || cfg->system == NULL) { return ESP_ERR_INVALID_ARG; }
    if (s_cfg.hops != NULL) { return ESP_ERR_INVALID_STATE; }
    s_cfg = *cfg;
    s_block = heap_caps_malloc(GEN_VISION_NOTE_CARD_BLOCK_BYTES, MALLOC_CAP_SPIRAM);
    s_hop = heap_caps_malloc(sizeof(*s_hop), MALLOC_CAP_SPIRAM);
    s_start_hops = (uint32_t)(GEN_VISION_NOTE_START_S * GEN_GRID_FRAMES_PER_S);
    s_end_hops = (uint32_t)(GEN_VISION_NOTE_END_SILENCE_S * GEN_GRID_FRAMES_PER_S);
    s_max_hops = (uint32_t)(GEN_VISION_NOTE_MAX_S * GEN_GRID_FRAMES_PER_S);
    return s_block != NULL && s_hop != NULL ? ESP_OK : ESP_ERR_NO_MEM;
}

static void put_le(uint8_t *out, uint32_t value, size_t bytes)
{
    for (size_t i = 0; i < bytes; ++i) {
        out[i] = (uint8_t)(value >> (8 * i));
    }
}

// Microsoft's canonical 44-byte header: RIFF, a 16-byte PCM fmt chunk, then data.
static void wav_header(uint8_t *out, uint32_t pcm_bytes)
{
    memcpy(out, "RIFF", 4);
    put_le(out + 4, WAV_HEADER_BYTES - 8 + pcm_bytes, 4);
    memcpy(out + 8, "WAVEfmt ", 8);
    put_le(out + 16, WAV_FMT_BYTES, 4);
    put_le(out + 20, WAV_PCM, 2);
    put_le(out + 22, WAV_CHANNELS, 2);
    put_le(out + 24, GEN_GRID_SAMPLE_RATE_HZ, 4);
    put_le(out + 28, GEN_GRID_SAMPLE_RATE_HZ * WAV_CHANNELS * WAV_BYTES_PER_SAMPLE, 4);
    put_le(out + 32, WAV_CHANNELS * WAV_BYTES_PER_SAMPLE, 2);
    put_le(out + 34, WAV_BITS, 2);
    memcpy(out + 36, "data", 4);
    put_le(out + 40, pcm_bytes, 4);
}

static bool over(const taken_t *taken, bool heard, uint32_t quiet)
{
    return taken->hops >= s_max_hops || (!heard && taken->hops >= s_start_hops) ||
           (heard && quiet >= s_end_hops);
}

static bool append(size_t *filled, taken_t *taken, bool kept)
{
    kept = kept && drv_sd_append(s_path, s_block, *filled) == ESP_OK;
    taken->pcm_bytes += (uint32_t)*filled;
    *filled = 0;
    return kept;
}

static bool take_hops(taken_t *taken, bool kept)
{
    const int64_t give_up_us = esp_timer_get_time() + (int64_t)((GEN_VISION_NOTE_MAX_S + SLACK_S) * US_PER_S);
    bool heard = false;
    uint32_t quiet = 0, next_seq = 0;
    size_t filled = 0;
    while (!over(taken, heard, quiet) && esp_timer_get_time() < give_up_us) {
        if (xStreamBufferReceive(s_cfg.hops, s_hop, sizeof(*s_hop), pdMS_TO_TICKS(HOP_WAIT_MS)) !=
            sizeof(*s_hop)) {
            continue;
        }
        if (taken->hops > 0 && s_hop->seq != next_seq) { taken->dropped += s_hop->seq - next_seq; }
        next_seq = s_hop->seq + 1;
        taken->hops++;
        // The speaker's own beep is neither speech nor quiet (KEHOACH 3.19).
        if ((s_hop->flags & APP_NOTE_DEAF) == 0) {
            heard = heard || (s_hop->flags & APP_NOTE_VAD) != 0;
            quiet = (s_hop->flags & APP_NOTE_VAD) != 0 ? 0 : quiet + (heard ? 1 : 0);
        }
        memcpy(s_block + filled, s_hop->pcm, sizeof(s_hop->pcm));
        filled += sizeof(s_hop->pcm);
        if (filled + sizeof(s_hop->pcm) > GEN_VISION_NOTE_CARD_BLOCK_BYTES) {
            kept = append(&filled, taken, kept);
        }
    }
    if (filled > 0) { kept = append(&filled, taken, kept); }
    return kept;
}

void svc_note_record(uint32_t seq, svc_note_t *note)
{
    *note = (svc_note_t){0};
    s_seq = seq;
    s_saved_bytes = 0;
    char stem[PATH_BYTES];
    snprintf(stem, sizeof(stem), "IMG/B%" PRIu32 "/%06" PRIu32, s_cfg.boot, seq);
    if (s_block == NULL || s_hop == NULL || !drv_sd_ready() ||
        drv_sd_name(stem, "WAV", s_path, sizeof(s_path)) != ESP_OK) {
        ESP_LOGW(TAG, "B%" PRIu32 "/%06" PRIu32 ": no card or no buffers, no note", s_cfg.boot, seq);
        return;
    }
    uint8_t header[WAV_HEADER_BYTES];
    wav_header(header, 0);
    const bool begun = drv_sd_write(s_path, header, sizeof(header)) == ESP_OK;
    const int64_t started_us = esp_timer_get_time();
    taken_t taken = {0};
    xStreamBufferReset(s_cfg.hops);
    bsp_board_camera_light(true);
    xEventGroupSetBits(s_cfg.system, APP_BIT_NOTE_ON);
    const bool kept = take_hops(&taken, begun);
    xEventGroupClearBits(s_cfg.system, APP_BIT_NOTE_ON);
    bsp_board_camera_light(false);
    wav_header(header, taken.pcm_bytes);
    note->saved = kept && drv_sd_patch(s_path, 0, header, sizeof(header)) == ESP_OK;
    note->ms = (uint32_t)(taken.hops * MS_PER_S / GEN_GRID_FRAMES_PER_S);
    note->dropped = taken.dropped;
    note->bytes = note->saved ? WAV_HEADER_BYTES + taken.pcm_bytes : 0;
    s_saved_bytes = note->bytes;
    ESP_LOGI(TAG,
             "B%" PRIu32 "/%06" PRIu32 ": %" PRIu32 " hops, %" PRIu32 " missing, %" PRIu32
             " bytes, %s, %lld ms",
             s_cfg.boot, seq, taken.hops, taken.dropped, taken.pcm_bytes, note->saved ? "kept" : "not kept",
             (esp_timer_get_time() - started_us) / US_PER_MS);
}

static esp_err_t fill_from_card(const void *ctx, size_t offset, uint8_t *data, size_t bytes)
{
    size_t got = 0;
    const esp_err_t err = drv_sd_read(ctx, offset, data, bytes, &got);
    if (err != ESP_OK) { return err; }
    return got == bytes ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

esp_err_t svc_note_send(void)
{
    if (s_saved_bytes == 0) { return ESP_ERR_INVALID_STATE; }
    const net_mqtt_chunks_t chunks = {
        .topic = GEN_TOPIC_NOTE,
        .boot = s_cfg.boot,
        .seq = s_seq,
        .total_bytes = s_saved_bytes,
        .fill = fill_from_card,
        .ctx = s_path,
    };
    const esp_err_t err = net_mqtt_publish_chunks(&chunks);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "B%" PRIu32 "/%06" PRIu32 " not sent: %s", s_cfg.boot, s_seq, esp_err_to_name(err));
    }
    return err;
}
