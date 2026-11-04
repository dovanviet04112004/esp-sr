#include "drv_gps/nmea.h"

#include <stddef.h>
#include <stdlib.h>
#include <string.h>

#define FIELDS_MAX 21
#define E7 10000000.0
#define MINUTES_PER_DEGREE 60.0
#define TALKER_BYTES 2 // the GP or GN ahead of the sentence type

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

static void read_rmc(drv_gps_fix_t *fix, char *fields[], size_t count)
{
    if (count < 7) { return; }
    fix->has_fix = fields[2][0] == 'A';
    if (fix->has_fix) {
        fix->lat_e7 = degrees_e7(fields[3], fields[4][0]);
        fix->lon_e7 = degrees_e7(fields[5], fields[6][0]);
    }
}

static void read_sentence(drv_gps_fix_t *fix, char *line)
{
    char *fields[FIELDS_MAX];
    const size_t count = split(line, fields);
    if (strlen(fields[0]) < 1 + TALKER_BYTES) { return; }
    const char *type = fields[0] + 1 + TALKER_BYTES;
    if (strcmp(type, "RMC") == 0) {
        read_rmc(fix, fields, count);
    } else if (strcmp(type, "GGA") == 0 && count > 7) {
        fix->satellites = (uint8_t)atoi(fields[7]);
    } else if (strcmp(type, "GSV") == 0 && count > 3) {
        const int in_view = atoi(fields[3]);
        if (in_view > fix->in_view) { fix->in_view = (uint8_t)in_view; }
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
    read_sentence(&nmea->fix, nmea->line);
    return true;
}
