#include "drv_gps/nmea.h"

#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#include "gen_vision.h"

#define FIELDS_MAX 21
#define E7 10000000.0
#define E1 10.0
#define MINUTES_PER_DEGREE 60.0
#define TALKER_BYTES 2 // the GP or GN ahead of the sentence type
#define RMC_TIME 1
#define RMC_STATUS 2
#define RMC_DATE 9
#define RMC_FIELDS_MIN 10
#define RMC_MODE 12
#define RMC_MODE_FIELDS_MIN 14 // field 12 and the checksum behind it
#define GGA_QUALITY 6
#define GGA_SATELLITES 7
#define GGA_HDOP 8
#define GSV_IN_VIEW 3
#define HDOP_E1_MAX 9999
#define PAIR_DIGITS 2
#define CENTURY_2000 2000
#define CENTURY_1900 1900
#define YY_PIVOT 80 // two-digit years from 80 are 19yy, as GPS began in 1980
#define MONTHS 12
#define HOURS 24
#define MINUTES 60
#define S_PER_MIN 60
#define S_PER_HOUR 3600
#define S_PER_DAY 86400
#define DAYS_FEB_LEAP 29

static int hex_value(char c)
{
    if (c >= '0' && c <= '9') { return c - '0'; }
    if (c >= 'A' && c <= 'F') { return c - 'A' + 10; }
    return -1;
}

static bool checksum_holds(const char *line, size_t length)
{
    if (length < 4 || line[0] != '$' || line[length - 3] != '*') { return false; }
    uint8_t sum = 0;
    for (size_t i = 1; i < length - 3; i++) {
        sum ^= (uint8_t)line[i];
    }
    const int high = hex_value(line[length - 2]), low = hex_value(line[length - 1]);
    return high >= 0 && low >= 0 && sum == (uint8_t)(high << 4 | low);
}

static size_t split(char *line, char *fields[FIELDS_MAX])
{
    size_t count = 0;
    fields[count++] = line;
    for (char *c = line; *c != '\0' && count < FIELDS_MAX; c++) {
        if (*c == ',' || *c == '*') {
            *c = '\0';
            fields[count++] = c + 1;
        }
    }
    return count;
}

// ddmm.mmmm or dddmm.mmmm, the minutes behind the last two digits ahead of the point.
static int32_t degrees_e7(const char *text, char hemisphere)
{
    const double value = strtod(text, NULL);
    const int degrees = (int)(value / 100.0);
    const double scaled = (degrees + (value - degrees * 100.0) / MINUTES_PER_DEGREE) * E7;
    const int32_t rounded = (int32_t)(scaled + 0.5);
    return hemisphere == 'S' || hemisphere == 'W' ? -rounded : rounded;
}

static int two_digits(const char *text)
{
    if (text[0] < '0' || text[0] > '9' || text[1] < '0' || text[1] > '9') { return -1; }
    return (text[0] - '0') * 10 + (text[1] - '0');
}

static bool is_leap(int year)
{
    return (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
}

static int days_in_month(int year, int month)
{
    static const int days[MONTHS] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
    return month == 2 && is_leap(year) ? DAYS_FEB_LEAP : days[month - 1];
}

// Days from 1970-01-01 to a civil date, by H. Hinnant's days_from_civil.
static int32_t days_from_civil(int year, int month, int day)
{
    year -= month <= 2;
    const int era = year / 400;
    const int year_of_era = year - era * 400;
    const int day_of_year = (153 * (month + (month > 2 ? -3 : 9)) + 2) / 5 + day - 1;
    const int day_of_era = year_of_era * 365 + year_of_era / 4 - year_of_era / 100 + day_of_year;
    return era * 146097 + day_of_era - 719468;
}

// hhmmss may carry .sss, ignored; false for an empty, malformed or too early field.
static bool rmc_utc_s(const char *hhmmss, const char *ddmmyy, uint32_t *utc_s)
{
    if (strlen(hhmmss) < 3 * PAIR_DIGITS || strlen(ddmmyy) != 3 * PAIR_DIGITS) { return false; }
    const int hour = two_digits(hhmmss), minute = two_digits(hhmmss + PAIR_DIGITS),
              second = two_digits(hhmmss + 2 * PAIR_DIGITS);
    const int day = two_digits(ddmmyy), month = two_digits(ddmmyy + PAIR_DIGITS),
              yy = two_digits(ddmmyy + 2 * PAIR_DIGITS);
    if (hour < 0 || hour >= HOURS || minute < 0 || minute >= MINUTES || second < 0 || second >= S_PER_MIN) {
        return false;
    }
    if (yy < 0 || month < 1 || month > MONTHS) { return false; }
    const int year = yy + (yy < YY_PIVOT ? CENTURY_2000 : CENTURY_1900);
    if (year < GEN_VISION_CLOCK_YEAR_MIN || day < 1 || day > days_in_month(year, month)) { return false; }
    *utc_s = (uint32_t)days_from_civil(year, month, day) * S_PER_DAY + (uint32_t)(hour * S_PER_HOUR) +
             (uint32_t)(minute * S_PER_MIN + second);
    return true;
}

static void read_rmc(drv_gps_fix_t *fix, char *fields[], size_t count)
{
    if (count < 7) { return; }
    fix->has_fix = fields[RMC_STATUS][0] == 'A';
    if (fix->has_fix) {
        fix->lat_e7 = degrees_e7(fields[3], fields[4][0]);
        fix->lon_e7 = degrees_e7(fields[5], fields[6][0]);
    }
    fix->has_time = count >= RMC_FIELDS_MIN && rmc_utc_s(fields[RMC_TIME], fields[RMC_DATE], &fix->utc_s);
    const char mode = count >= RMC_MODE_FIELDS_MIN ? fields[RMC_MODE][0] : '\0';
    fix->mode = mode >= 'A' && mode <= 'Z' ? mode : '\0';
}

static void read_gga(drv_gps_fix_t *fix, char *fields[], size_t count)
{
    if (count <= GGA_HDOP) { return; }
    fix->satellites = (uint8_t)atoi(fields[GGA_SATELLITES]);
    const char quality = fields[GGA_QUALITY][0];
    fix->has_quality = quality >= '0' && quality <= '9';
    fix->fix_quality = fix->has_quality ? (uint8_t)(quality - '0') : 0;
    char *end = NULL;
    const double hdop = strtod(fields[GGA_HDOP], &end);
    fix->has_hdop = end != fields[GGA_HDOP] && hdop >= 0.0;
    const double hdop_e1 = hdop * E1 + 0.5;
    fix->hdop_e1 = !fix->has_hdop ? 0 : hdop_e1 > HDOP_E1_MAX ? HDOP_E1_MAX : (uint16_t)hdop_e1;
}

static void read_gsv(drv_gps_nmea_t *nmea, const char *talker, const char *in_view_text)
{
    drv_gps_nmea_system_t *slot = NULL;
    for (size_t i = 0; i < DRV_GPS_NMEA_SYSTEMS_MAX && slot == NULL; i++) {
        drv_gps_nmea_system_t *at = &nmea->systems[i];
        const bool empty = at->talker[0] == '\0';
        if (empty || memcmp(at->talker, talker, TALKER_BYTES) == 0) {
            memcpy(at->talker, talker, TALKER_BYTES);
            slot = at;
        }
    }
    if (slot == NULL) { return; }
    const int in_view = atoi(in_view_text);
    slot->in_view = in_view < 0 ? 0 : in_view > UINT8_MAX ? UINT8_MAX : (uint8_t)in_view;
    int sum = 0;
    for (size_t i = 0; i < DRV_GPS_NMEA_SYSTEMS_MAX; i++) {
        sum += nmea->systems[i].in_view;
    }
    nmea->fix.in_view = sum > UINT8_MAX ? UINT8_MAX : (uint8_t)sum;
}

static void read_sentence(drv_gps_nmea_t *nmea, char *line)
{
    drv_gps_fix_t *fix = &nmea->fix;
    char *fields[FIELDS_MAX];
    const size_t count = split(line, fields);
    if (strlen(fields[0]) < 1 + TALKER_BYTES) { return; }
    const char *talker = fields[0] + 1;
    const char *type = talker + TALKER_BYTES;
    if (strcmp(type, "RMC") == 0) {
        read_rmc(fix, fields, count);
    } else if (strcmp(type, "GGA") == 0) {
        read_gga(fix, fields, count);
    } else if (strcmp(type, "GSV") == 0 && count > GSV_IN_VIEW) {
        read_gsv(nmea, talker, fields[GSV_IN_VIEW]);
    } else if (strcmp(type, "TXT") == 0 && count > 4 && strncmp(fields[4], "ANTENNA", 7) == 0) {
        fix->antenna_open = strcmp(fields[4], "ANTENNA OPEN") == 0;
    }
}

void drv_gps_nmea_reset(drv_gps_nmea_t *nmea)
{
    memset(nmea, 0, sizeof(*nmea));
}

bool drv_gps_nmea_feed(drv_gps_nmea_t *nmea, uint8_t byte)
{
    if (byte == '$') {
        nmea->length = 0;
        nmea->overflowed = false;
    }
    if (byte != '\r' && byte != '\n') {
        if (nmea->length < sizeof(nmea->line) - 1) {
            nmea->line[nmea->length++] = (char)byte;
        } else {
            nmea->overflowed = true;
        }
        return false;
    }
    if (nmea->length == 0) { return false; }
    const size_t length = nmea->length;
    nmea->length = 0;
    nmea->line[length] = '\0';
    if (nmea->overflowed || !checksum_holds(nmea->line, length)) {
        nmea->rejected++;
        return false;
    }
    nmea->sentences++;
    read_sentence(nmea, nmea->line);
    return true;
}
