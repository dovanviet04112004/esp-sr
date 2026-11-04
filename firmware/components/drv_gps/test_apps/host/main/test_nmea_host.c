#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "drv_gps/nmea.h"

static int feed(drv_gps_nmea_t *nmea, const char *text)
{
    int passed = 0;
    for (const char *c = text; *c != '\0'; c++) {
        passed += drv_gps_nmea_feed(nmea, (uint8_t)*c);
    }
    return passed;
}

static void test_a_receiver_without_a_fix_says_so(void)
{
    drv_gps_nmea_t nmea;
    drv_gps_nmea_reset(&nmea);
    const int passed = feed(&nmea, "$GNRMC,,V,,,,,,,,,,N*4D\r\n$GPTXT,01,01,01,ANTENNA OPEN*25\r\n"
                                   "$GPGSV,1,1,04,06,,,27,21,,,27,28,,,32,193,,,30*4B\r\n");
    assert(passed == 3);
    assert(!nmea.fix.has_fix);
    assert(nmea.fix.antenna_open);
    assert(nmea.fix.in_view == 4);
}

static void test_a_fix_reads_as_signed_degrees(void)
{
    drv_gps_nmea_t nmea;
    drv_gps_nmea_reset(&nmea);
    const int passed =
        feed(&nmea, "$GNRMC,092750.000,A,5321.6802,S,00630.3372,W,0.02,31.66,280511,,,A*40\r\n"
                    "$GNGGA,092750.000,5321.6802,S,00630.3372,W,1,08,1.03,61.7,M,55.2,M,,*45\r\n");
    assert(passed == 2);
    assert(nmea.fix.has_fix);
    assert(nmea.fix.lat_e7 == -533613367);
    assert(nmea.fix.lon_e7 == -65056200);
    assert(nmea.fix.satellites == 8);
}

static void test_a_bad_checksum_changes_nothing(void)
{
    drv_gps_nmea_t nmea;
    drv_gps_nmea_reset(&nmea);
    assert(feed(&nmea, "$GNRMC,092750.000,A,5321.6802,S,00630.3372,W,0.02,31.66,280511,,,A*5F\r\n") == 0);
    assert(!nmea.fix.has_fix);
    assert(nmea.rejected == 1);
}

static void test_a_line_too_long_is_refused_and_the_next_one_reads(void)
{
    drv_gps_nmea_t nmea;
    drv_gps_nmea_reset(&nmea);
    char long_line[DRV_GPS_NMEA_LINE_MAX_BYTES + 16];
    memset(long_line, 'A', sizeof(long_line) - 3);
    long_line[0] = '$';
    strcpy(&long_line[sizeof(long_line) - 3], "\r\n");
    assert(feed(&nmea, long_line) == 0);
    assert(nmea.rejected == 1);
    assert(feed(&nmea, "$GPTXT,01,01,01,ANTENNA OPEN*25\r\n") == 1);
}

int main(void)
{
    test_a_receiver_without_a_fix_says_so();
    test_a_fix_reads_as_signed_degrees();
    test_a_bad_checksum_changes_nothing();
    test_a_line_too_long_is_refused_and_the_next_one_reads();
    puts("nmea: all passed");
    return 0;
}
