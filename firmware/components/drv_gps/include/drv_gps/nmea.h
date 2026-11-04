/** NMEA 0183 sentences into a fix, with no OS under it (KEHOACH 3.19).
 *  @ctx one task | non-blocking | the caller owns the state
 */
#pragma once

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define DRV_GPS_NMEA_LINE_MAX_BYTES 96 // NMEA caps a sentence at 82, some receivers run over

typedef struct {
    bool has_fix;
    int32_t lat_e7;     // degrees times 1e7, north positive
    int32_t lon_e7;     // degrees times 1e7, east positive
    uint8_t satellites; // in use, from GGA
    uint8_t in_view;    // the largest count a GSV reported since the last read
    bool antenna_open;  // the receiver's own TXT says nothing is on the antenna pin
} drv_gps_fix_t;

typedef struct {
    char line[DRV_GPS_NMEA_LINE_MAX_BYTES];
    uint8_t length;
    bool overflowed;
    uint32_t sentences; // passed their checksum
    uint32_t rejected;  // failed it, or ran over the line
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
