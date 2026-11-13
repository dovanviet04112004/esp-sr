#include "svc_sensors.h"

#include <stdbool.h>
#include <string.h>

#include "drv_gps.h"
#include "drv_i2c.h"
#include "drv_i2c/ads1115.h"
#include "drv_i2c/mpu6500.h"
#include "drv_ir.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "gen_payload.h"
#include "gen_vision.h"
#include "net_mqtt.h"
#include "sys_time.h"

#define US_PER_S 1000000
#define MS_PER_S 1000
#define TICKS_PER_REPORT (GEN_VISION_SENSORS_REPORT_S * MS_PER_S / GEN_VISION_SENSORS_PERIOD_MS)

static const char *TAG = "svc_sensors";

static struct {
    uint32_t boot;
    uint32_t ticks;
    bool motion_ready;
    bool adc_ready;
    bool clock_reported; // the last report carried utcMs
    drv_i2c_ads1115_t adc;
    sensors_gps_t gps;
    sensors_ir_t ir;
} s_sensors;

static sensors_t s_latest;
static bool s_read_once;
static portMUX_TYPE s_latest_lock = portMUX_INITIALIZER_UNLOCKED;

static void log_failure(const char *device, esp_err_t err)
{
    if (err != ESP_OK) { ESP_LOGE(TAG, "no %s: %s", device, esp_err_to_name(err)); }
}

void svc_sensors_init(const svc_sensors_config_t *cfg)
{
    s_sensors.boot = cfg->boot;
    log_failure("gps", drv_gps_init());
    log_failure("infrared", drv_ir_init());
    esp_err_t err = drv_i2c_init();
    log_failure("i2c", err);
    if (err != ESP_OK) { return; }
    err = drv_i2c_mpu6500_init();
    log_failure("mpu6500", err);
    s_sensors.motion_ready = err == ESP_OK;
    err = drv_i2c_ads1115_init(&s_sensors.adc);
    log_failure("ads1115", err);
    s_sensors.adc_ready = err == ESP_OK;
}

static void read_gps(sensors_gps_t *gps)
{
    drv_gps_fix_t fix;
    drv_gps_poll(&fix);
    if (fix.has_time) { sys_time_offer(fix.utc_s, esp_timer_get_time()); }
    *gps = (sensors_gps_t){
        .has_fix = fix.has_fix,
        .lat_e7 = fix.lat_e7,
        .lon_e7 = fix.lon_e7,
        .satellites = fix.satellites,
        .in_view = fix.in_view,
        .antenna_open = fix.antenna_open,
        .hdop_e1 = fix.hdop_e1,
        .has_hdop_e1 = fix.has_hdop,
        .fix_quality = fix.fix_quality,
        .has_fix_quality = fix.has_quality,
        .mode = {fix.mode, '\0'},
        .has_mode = fix.mode != '\0',
    };
}

static void read_motion(sensors_t *report)
{
    drv_i2c_mpu6500_sample_t sample;
    report->has_motion = s_sensors.motion_ready && drv_i2c_mpu6500_read(&sample) == ESP_OK;
    if (!report->has_motion) { return; }
    report->motion.accel_mg_count = DRV_I2C_MPU6500_AXES;
    report->motion.gyro_dps_e1_count = DRV_I2C_MPU6500_AXES;
    memcpy(report->motion.accel_mg, sample.accel_mg, sizeof(sample.accel_mg));
    memcpy(report->motion.gyro_dps_e1, sample.gyro_dps_e1, sizeof(sample.gyro_dps_e1));
    report->motion.temp_ce1 = sample.temp_c_e1;
}

static void read_adc(sensors_t *report)
{
    report->has_adc = s_sensors.adc_ready && drv_i2c_ads1115_step(&s_sensors.adc) == ESP_OK;
    if (!report->has_adc) { return; }
    report->adc.channels_mv_count = DRV_I2C_ADS1115_CHANNELS;
    memcpy(report->adc.channels_mv, s_sensors.adc.channel_mv, sizeof(s_sensors.adc.channel_mv));
}

static bool something_changed(const sensors_t *report)
{
    return report->ir.active != s_sensors.ir.active || report->ir.hits != s_sensors.ir.hits ||
           report->gps.has_fix != s_sensors.gps.has_fix ||
           report->gps.antenna_open != s_sensors.gps.antenna_open ||
           report->has_utc_ms != s_sensors.clock_reported;
}

void svc_sensors_step(void)
{
    sensors_t report = {.boot = s_sensors.boot, .uptime_s = (uint32_t)(esp_timer_get_time() / US_PER_S)};
    drv_ir_state_t ir;
    drv_ir_read(&ir);
    report.ir = (sensors_ir_t){.active = ir.active, .hits = ir.hits};
    read_gps(&report.gps);
    report.has_utc_ms = sys_time_utc_ms(esp_timer_get_time(), &report.utc_ms);
    read_motion(&report);
    read_adc(&report);
    taskENTER_CRITICAL(&s_latest_lock);
    s_latest = report;
    s_read_once = true;
    taskEXIT_CRITICAL(&s_latest_lock);
    s_sensors.ticks++;
    if (!something_changed(&report) && s_sensors.ticks < TICKS_PER_REPORT) { return; }
    s_sensors.gps = report.gps;
    s_sensors.ir = report.ir;
    s_sensors.clock_reported = report.has_utc_ms;
    s_sensors.ticks = 0;
    net_mqtt_publish_sensors(&report);
}

bool svc_sensors_latest(sensors_t *out)
{
    taskENTER_CRITICAL(&s_latest_lock);
    const bool any = s_read_once;
    if (any) { *out = s_latest; }
    taskEXIT_CRITICAL(&s_latest_lock);
    return any;
}
