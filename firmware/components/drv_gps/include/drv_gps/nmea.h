/** NMEA 0183 sentences into a fix and a UTC time, with no OS under it (KEHOACH 3.19).
 *  @ctx one task | non-blocking | the caller owns the state
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define DRV_GPS_NMEA_LINE_MAX_BYTES 96 // NMEA caps a sentence at 82, some receivers run over
#define DRV_GPS_NMEA_SYSTEMS_MAX 4     // GPS, BeiDou, GLONASS, Galileo: one GSV series each

typedef struct {
    bool has_fix;
    int32_t lat_e7;      // degrees times 1e7, north positive
    int32_t lon_e7;      // degrees times 1e7, east positive
    uint8_t satellites;  // in use, from GGA
    uint8_t in_view;     // each system's latest GSV total, summed
    bool antenna_open;   // the receiver's own TXT says nothing is on the antenna pin
    bool has_time;       // the latest RMC carried a date and time
    uint32_t utc_s;      // that RMC's second, Unix time
    bool has_hdop;       // the latest GGA carried an HDOP
    uint16_t hdop_e1;    // its HDOP times 10
    bool has_quality;    // the latest GGA carried a fix quality
    uint8_t fix_quality; // 0 none, 1 GPS, 2 differential, 6 estimated
    char mode;           // RMC field 12: A, D, E, N; 0 when absent
} drv_gps_fix_t;

typedef struct {
    char talker[2]; // GP, BD, GB, GL, GA
    uint8_t in_view;
} drv_gps_nmea_system_t;

typedef struct {
    char line[DRV_GPS_NMEA_LINE_MAX_BYTES];
    uint8_t length;
    bool overflowed;
    uint32_t sentences; // passed their checksum
    uint32_t rejected;  // failed it, or ran over the line
    drv_gps_nmea_system_t systems[DRV_GPS_NMEA_SYSTEMS_MAX];
    drv_gps_fix_t fix;
} drv_gps_nmea_t;

/** Start from nothing known.
 *  @ctx one task | non-blocking
 */
void drv_gps_nmea_reset(drv_gps_nmea_t *nmea);

/** Take one received byte.
 *  @ctx one task | non-blocking
 *  @ret true when it ended a sentence that passed its checksum
 */
bool drv_gps_nmea_feed(drv_gps_nmea_t *nmea, uint8_t byte);

#ifdef __cplusplus
}
#endif
