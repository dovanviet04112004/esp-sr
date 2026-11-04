#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "app_config.h"
#include "driver/gpio.h"
#include "driver/i2c_master.h"
#include "driver/i2s_std.h"
#include "driver/uart.h"
#include "drv_camera.h"
#include "drv_sd.h"
#include "esp_heap_caps.h"
#include "esp_rom_sys.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "gen_vision.h"
#include "jpeg_decoder.h"

#define COMMAND_MARK '!' // the console's restart line must not read as blocks
#define FS_HZ 16000
#define BLOCK_FRAMES 256
#define SKIP_BLOCKS 16 // the microphone's start-up and the ring's stale samples
#define TONE_HZ 1000
#define TONE_PERIOD (FS_HZ / TONE_HZ)
#define TONE_AMPLITUDE 0.1
#define QUIET_BLOCKS 62
#define TONE_BLOCKS 188
#define FULL_SCALE_24 8388608.0
#define I2C_FIRST_ADDRESS 0x08
#define I2C_LAST_ADDRESS 0x77
#define I2C_PROBE_MS 30
#define GPS_LISTEN_US 3000000
#define GPS_POLL_US 10
#define GPS_EDGES_ALIVE 20
#define GPS_BAUD 9600
#define GPS_TEXT_BYTES 400
#define GPS_READ_MS 2500
#define IR_WATCH_MS 8000
#define IR_POLL_MS 5
#define LAMP_BLINKS 3
#define LAMP_HALF_MS 700
#define PULL_SETTLE_US 2000
#define BASE64_LINE_CHARS 76
#define CHECK_FILE "CHECK/BOARD_C.JPG"
#define REST_CHECK_MS 2000

static i2s_chan_handle_t s_tx, s_rx;
static int32_t s_in[BLOCK_FRAMES * 2], s_out[BLOCK_FRAMES * 2];
static bool s_camera_up;

typedef struct {
    double sum, square, cos_sum, sin_sum;
    int32_t peak;
    int n;
} slot_t;

static int64_t now_ms(void)
{
    return esp_timer_get_time() / 1000;
}

static void heap_line(const char *label)
{
    printf("HEAP %s: internal free %u largest %u min %u, psram free %u\n", label,
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL),
           (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
}

static double dbfs(double ratio)
{
    return ratio > 0 ? 20.0 * log10(ratio) : -200.0;
}

static void pin_drive(int pin, const char *name)
{
    gpio_reset_pin(pin);
    gpio_set_direction(pin, GPIO_MODE_INPUT);
    gpio_set_pull_mode(pin, GPIO_PULLUP_ONLY);
    esp_rom_delay_us(PULL_SETTLE_US);
    const int up = gpio_get_level(pin);
    gpio_set_pull_mode(pin, GPIO_PULLDOWN_ONLY);
    esp_rom_delay_us(PULL_SETTLE_US);
    const int down = gpio_get_level(pin);
    gpio_set_pull_mode(pin, GPIO_FLOATING);
    printf("PIN %s gpio%d: pull-up %d, pull-down %d, %s\n", name, pin, up, down,
           up != down ? "floating" : (up ? "driven high" : "driven low"));
}

static void check_i2c(void)
{
    pin_drive(APP_I2C_SDA_GPIO, "SDA");
    pin_drive(APP_I2C_SCL_GPIO, "SCL");
    const i2c_master_bus_config_t cfg = {
        .i2c_port = -1,
        .sda_io_num = APP_I2C_SDA_GPIO,
        .scl_io_num = APP_I2C_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    i2c_master_bus_handle_t bus = NULL;
    const esp_err_t err = i2c_new_master_bus(&cfg, &bus);
    if (err != ESP_OK) {
        printf("I2C bus: %s\n", esp_err_to_name(err));
        return;
    }
    int found = 0;
    for (int address = I2C_FIRST_ADDRESS; address <= I2C_LAST_ADDRESS; ++address) {
        if (i2c_master_probe(bus, address, I2C_PROBE_MS) == ESP_OK) {
            printf("I2C device at 0x%02X\n", address);
            ++found;
        }
    }
    printf("I2C found %d device(s)\n", found);
    i2c_del_master_bus(bus);
}

static void check_gps(void)
{
    pin_drive(APP_GPS_RX_GPIO, "GPS");
    int edges = 0, last = gpio_get_level(APP_GPS_RX_GPIO);
    const int64_t until_us = esp_timer_get_time() + GPS_LISTEN_US;
    while (esp_timer_get_time() < until_us) {
        const int now = gpio_get_level(APP_GPS_RX_GPIO);
        edges += now != last;
        last = now;
        esp_rom_delay_us(GPS_POLL_US);
    }
    printf("GPS %d edges in 3 s\n", edges);
    if (edges < GPS_EDGES_ALIVE) { return; }
    const uart_config_t cfg = {.baud_rate = GPS_BAUD,
                               .data_bits = UART_DATA_8_BITS,
                               .parity = UART_PARITY_DISABLE,
                               .stop_bits = UART_STOP_BITS_1,
                               .flow_ctrl = UART_HW_FLOWCTRL_DISABLE,
                               .source_clk = UART_SCLK_DEFAULT};
    uart_driver_install(UART_NUM_1, 1024, 0, 0, NULL, 0);
    uart_param_config(UART_NUM_1, &cfg);
    uart_set_pin(UART_NUM_1, UART_PIN_NO_CHANGE, APP_GPS_RX_GPIO, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE);
    static char text[GPS_TEXT_BYTES + 1];
    const int got = uart_read_bytes(UART_NUM_1, text, GPS_TEXT_BYTES, pdMS_TO_TICKS(GPS_READ_MS));
    uart_driver_delete(UART_NUM_1);
    for (int i = 0; i < got; ++i) {
        text[i] = (text[i] >= ' ' && text[i] < 127) || text[i] == '\n' ? text[i] : '.';
    }
    text[got > 0 ? got : 0] = '\0';
    printf("GPS %d bytes: %s\n", got, text);
}

static void check_ir(void)
{
    pin_drive(APP_IR_GPIO, "IR");
    gpio_set_pull_mode(APP_IR_GPIO, GPIO_PULLUP_ONLY);
    int last = gpio_get_level(APP_IR_GPIO), changes = 0;
    const int64_t started_ms = now_ms();
    while (now_ms() - started_ms < IR_WATCH_MS) {
        const int level = gpio_get_level(APP_IR_GPIO);
        changes += level != last;
        last = level;
        vTaskDelay(pdMS_TO_TICKS(IR_POLL_MS));
    }
    printf("IR %d change(s) in %d ms, level %d\n", changes, IR_WATCH_MS, last);
}

static void check_lamp(void)
{
    gpio_reset_pin(APP_LED_GPIO);
    gpio_set_direction(APP_LED_GPIO, GPIO_MODE_OUTPUT);
    for (int i = 0; i < LAMP_BLINKS; ++i) {
        gpio_set_level(APP_LED_GPIO, 1);
        vTaskDelay(pdMS_TO_TICKS(LAMP_HALF_MS));
        gpio_set_level(APP_LED_GPIO, 0);
        vTaskDelay(pdMS_TO_TICKS(LAMP_HALF_MS));
    }
    printf("LAMP blinked %d times\n", LAMP_BLINKS);
}

static esp_err_t audio_up(void)
{
    if (s_rx != NULL) { return ESP_OK; }
    i2s_chan_config_t chan = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan.dma_desc_num = 6;
    chan.dma_frame_num = BLOCK_FRAMES;
    chan.auto_clear_after_cb = true;
    esp_err_t err = i2s_new_channel(&chan, &s_tx, &s_rx);
    if (err != ESP_OK) { return err; }
    const i2s_std_config_t std = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(FS_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {.mclk = I2S_GPIO_UNUSED,
                     .bclk = APP_I2S_BCLK_GPIO,
                     .ws = APP_I2S_WS_GPIO,
                     .dout = APP_I2S_DOUT_GPIO,
                     .din = APP_I2S_DIN_GPIO},
    };
    if ((err = i2s_channel_init_std_mode(s_tx, &std)) != ESP_OK ||
        (err = i2s_channel_init_std_mode(s_rx, &std)) != ESP_OK ||
        (err = i2s_channel_enable(s_tx)) != ESP_OK || (err = i2s_channel_enable(s_rx)) != ESP_OK) {
        return err;
    }
    return ESP_OK;
}

static void slot_take(slot_t *slot, int32_t raw, int phase)
{
    const int32_t x = raw >> 8;
    const int32_t magnitude = x < 0 ? -x : x;
    slot->peak = magnitude > slot->peak ? magnitude : slot->peak;
    slot->sum += x;
    slot->square += (double)x * x;
    ++slot->n;
    const double angle = 2.0 * M_PI * phase / TONE_PERIOD;
    slot->cos_sum += x * cos(angle);
    slot->sin_sum += x * sin(angle);
}

static void slot_print(const char *label, const char *name, const slot_t *slot)
{
    const double mean = slot->sum / slot->n;
    const double rms = sqrt(fmax(slot->square / slot->n - mean * mean, 0.0));
    const double tone = 2.0 * sqrt(slot->cos_sum * slot->cos_sum + slot->sin_sum * slot->sin_sum) / slot->n;
    printf("%s %s: rms %.1f dBFS, peak %.1f dBFS, %d Hz %.1f dBFS\n", label, name, dbfs(rms / FULL_SCALE_24),
           dbfs(slot->peak / FULL_SCALE_24), TONE_HZ, dbfs(tone / FULL_SCALE_24));
}

static void listen(const char *label, int blocks, double amplitude)
{
    slot_t left = {0}, right = {0};
    int phase = 0;
    for (int b = 0; b < SKIP_BLOCKS + blocks; ++b) {
        size_t done = 0;
        if (amplitude > 0) {
            for (int i = 0; i < BLOCK_FRAMES; ++i) {
                const double angle = 2.0 * M_PI * ((phase + i) % TONE_PERIOD) / TONE_PERIOD;
                s_out[2 * i] = s_out[2 * i + 1] = (int32_t)(amplitude * 2147483647.0 * sin(angle));
            }
            i2s_channel_write(s_tx, s_out, sizeof(s_out), &done, 1000);
        }
        i2s_channel_read(s_rx, s_in, sizeof(s_in), &done, 1000);
        for (int i = 0; b >= SKIP_BLOCKS && i < BLOCK_FRAMES; ++i) {
            slot_take(&left, s_in[2 * i], (phase + i) % TONE_PERIOD);
            slot_take(&right, s_in[2 * i + 1], (phase + i) % TONE_PERIOD);
        }
        phase = (phase + BLOCK_FRAMES) % TONE_PERIOD;
    }
    slot_print(label, "left", &left);
    slot_print(label, "right", &right);
}

static void check_audio(void)
{
    const esp_err_t err = audio_up();
    if (err != ESP_OK) {
        printf("I2S: %s\n", esp_err_to_name(err));
        return;
    }
    listen("AUDIO quiet", QUIET_BLOCKS, 0.0);
    listen("AUDIO tone", TONE_BLOCKS, TONE_AMPLITUDE);
    listen("AUDIO after", QUIET_BLOCKS, 0.0);
}

static void print_base64(const uint8_t *data, size_t bytes)
{
    static const char kTable[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    char line[BASE64_LINE_CHARS + 1];
    size_t at = 0;
    for (size_t i = 0; i < bytes; i += 3) {
        const uint32_t v = (uint32_t)data[i] << 16 | (uint32_t)(i + 1 < bytes ? data[i + 1] : 0) << 8 |
                           (i + 2 < bytes ? data[i + 2] : 0);
        line[at++] = kTable[v >> 18 & 63];
        line[at++] = kTable[v >> 12 & 63];
        line[at++] = i + 1 < bytes ? kTable[v >> 6 & 63] : '=';
        line[at++] = i + 2 < bytes ? kTable[v & 63] : '=';
        if (at == BASE64_LINE_CHARS) {
            line[at] = '\0';
            puts(line);
            at = 0;
        }
    }
    line[at] = '\0';
    puts(line);
}

static void decode_half(const drv_camera_frame_t *frame)
{
    const size_t width = frame->width / GEN_VISION_DECODE_SCALE,
                 height = frame->height / GEN_VISION_DECODE_SCALE;
    const size_t bytes = width * height * 3;
    uint8_t *rgb = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM);
    esp_jpeg_image_cfg_t decode = {
        .indata = (uint8_t *)frame->jpeg,
        .indata_size = frame->bytes,
        .outbuf = rgb,
        .outbuf_size = bytes,
        .out_format = JPEG_IMAGE_FORMAT_RGB888,
        .out_scale = JPEG_IMAGE_SCALE_1_2,
    };
    esp_jpeg_image_output_t out = {0};
    const int64_t started_ms = now_ms();
    const esp_err_t err = esp_jpeg_decode(&decode, &out);
    printf("CAMERA decode: %s, %ux%u, %lld ms\n", esp_err_to_name(err), out.width, out.height,
           (long long)(now_ms() - started_ms));
    heap_caps_free(rgb);
}

static void store(const drv_camera_frame_t *frame)
{
    if (!drv_sd_ready()) {
        printf("SD no card\n");
        return;
    }
    const int64_t started_ms = now_ms();
    const esp_err_t err = drv_sd_write(CHECK_FILE, frame->jpeg, frame->bytes);
    printf("SD write %u bytes: %s, %lld ms\n", (unsigned)frame->bytes, esp_err_to_name(err),
           (long long)(now_ms() - started_ms));
}

static void shot(const char *label)
{
    int64_t mark_ms = now_ms();
    esp_err_t err = drv_camera_wake();
    printf("%s wake: %s, %lld ms\n", label, esp_err_to_name(err), (long long)(now_ms() - mark_ms));
    mark_ms = now_ms();
    err = drv_camera_focus();
    printf("%s focus: %s, %lld ms\n", label, esp_err_to_name(err), (long long)(now_ms() - mark_ms));
    drv_camera_frame_t frame = {0};
    mark_ms = now_ms();
    err = drv_camera_grab(&frame);
    printf("%s grab: %s, %ux%u, %u bytes, %lld ms\n", label, esp_err_to_name(err), frame.width, frame.height,
           (unsigned)frame.bytes, (long long)(now_ms() - mark_ms));
    if (err != ESP_OK) { return; }
    decode_half(&frame);
    store(&frame);
    printf("JPEG_BEGIN %u\n", (unsigned)frame.bytes);
    print_base64(frame.jpeg, frame.bytes);
    printf("JPEG_END\n");
    drv_camera_release(&frame);
    printf("%s rest: %s\n", label, esp_err_to_name(drv_camera_rest()));
}

static void check_camera(void)
{
    heap_line("before");
    if (!s_camera_up) {
        const int64_t started_ms = now_ms();
        const esp_err_t err = drv_camera_init();
        printf("CAMERA init: %s, %lld ms\n", esp_err_to_name(err), (long long)(now_ms() - started_ms));
        heap_line("camera");
        if (err != ESP_OK) { return; }
        s_camera_up = true;
        printf("SD init: %s\n", esp_err_to_name(drv_sd_init()));
        heap_line("sd");
    }
    shot("CAMERA first");
    vTaskDelay(pdMS_TO_TICKS(REST_CHECK_MS));
    heap_line("resting");
    shot("CAMERA second");
    heap_line("after");
}

void app_main(void)
{
    uart_driver_install(UART_NUM_0, 256, 0, 0, NULL, 0);
    printf("BOARD_C ready\n");
    for (;;) {
        uint8_t mark = 0, block = 0;
        if (uart_read_bytes(UART_NUM_0, &mark, 1, pdMS_TO_TICKS(200)) != 1 || mark != COMMAND_MARK ||
            uart_read_bytes(UART_NUM_0, &block, 1, pdMS_TO_TICKS(200)) != 1) {
            continue;
        }
        switch (block) {
        case 'i': check_i2c(); break;
        case 'g': check_gps(); break;
        case 'r': check_ir(); break;
        case 'l': check_lamp(); break;
        case 'a': check_audio(); break;
        case 'c': check_camera(); break;
        default: continue;
        }
        printf("DONE %c\n", block);
    }
}
