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

static void test_a_time_without_a_fix_reads_as_unix_seconds(void)
{
    drv_gps_nmea_t nmea;
    drv_gps_nmea_reset(&nmea);
    assert(feed(&nmea, "$GNRMC,072746.000,V,,,,,,,131126,,,N*55\r\n") == 1);
    assert(!nmea.fix.has_fix);
    assert(nmea.fix.has_time);
    assert(nmea.fix.utc_s == 1794554866u);
}

static void test_a_fix_on_a_leap_day_reads_both(void)
{
    drv_gps_nmea_t nmea;
    drv_gps_nmea_reset(&nmea);
    assert(feed(&nmea, "$GNRMC,235959.000,A,2101.5000,N,10551.0000,E,0.00,0.00,290228,,,A*75\r\n") == 1);
    assert(nmea.fix.has_fix);
    assert(nmea.fix.has_time);
    assert(nmea.fix.utc_s == 1835481599u);
}

static void test_no_time_from_an_empty_early_or_impossible_field(void)
{
    static const struct {
        const char *line;
        const char *why;
    } refused[] = {
        {"$GNRMC,,V,,,,,,,,,,N*4D\r\n", "the bring-up line, nothing heard yet"},
        {"$GNRMC,072746.000,V,,,,,,,060180,,,N*5C\r\n", "1980, the receiver's own default"},
        {"$GNRMC,072746.000,V,,,,,,,310226,,,N*57\r\n", "31 February"},
        {"$GNRMC,072746.000,V,,,,,,,290227,,,N*5F\r\n", "29 February of a common year"},
        {"$GNRMC,240000.000,V,,,,,,,131126,,,N*53\r\n", "hour 24"},
        {"$GNRMC,072746,V,,,,,,,1311,,,N*4F\r\n", "a date without its year"},
    };
    for (size_t i = 0; i < sizeof(refused) / sizeof(refused[0]); i++) {
        drv_gps_nmea_t nmea;
        drv_gps_nmea_reset(&nmea);
        assert(feed(&nmea, "$GNRMC,072746.000,V,,,,,,,131126,,,N*55\r\n") == 1);
        assert(feed(&nmea, refused[i].line) == 1);
        if (nmea.fix.has_time) { printf("a time taken from %s\n", refused[i].why); }
        assert(!nmea.fix.has_time);
    }
}

static void test_satellites_in_view_add_up_over_systems(void)
{
    drv_gps_nmea_t nmea;
    drv_gps_nmea_reset(&nmea);
    assert(feed(&nmea, "$GPGSV,3,1,12,06,,,27,21,,,27,28,,,32,193,,,30*4E\r\n"
                       "$BDGSV,2,1,08,06,,,27,21,,,27,28,,,32,33,,,30*6E\r\n") == 2);
    assert(nmea.fix.in_view == 20);
    assert(feed(&nmea, "$GPGSV,3,1,10,06,,,27,21,,,27,28,,,32,193,,,30*4C\r\n") == 1);
    assert(nmea.fix.in_view == 18);
}

static void test_gga_gives_hdop_and_quality_and_rmc_its_mode(void)
{
    drv_gps_nmea_t nmea;
    drv_gps_nmea_reset(&nmea);
    assert(feed(&nmea, "$GNRMC,092750.000,A,5321.6802,S,00630.3372,W,0.02,31.66,280511,,,A*40\r\n"
                       "$GNGGA,092750.000,5321.6802,S,00630.3372,W,1,08,1.03,61.7,M,55.2,M,,*45\r\n") == 2);
    assert(nmea.fix.mode == 'A');
    assert(nmea.fix.has_hdop && nmea.fix.hdop_e1 == 10);
    assert(nmea.fix.has_quality && nmea.fix.fix_quality == 1);
    assert(feed(&nmea, "$GNRMC,092750.000,A,5321.6802,S,00630.3372,W,0.02,31.66,280511,,,E*44\r\n"
                       "$GNGGA,092750.000,5321.6802,S,00630.3372,W,6,04,25.5,61.7,M,55.2,M,,*4E\r\n") == 2);
    assert(nmea.fix.has_fix && nmea.fix.mode == 'E');
    assert(nmea.fix.hdop_e1 == 255 && nmea.fix.fix_quality == 6 && nmea.fix.satellites == 4);
}

static void test_empty_gga_fields_and_an_old_rmc_report_nothing(void)
{
    drv_gps_nmea_t nmea;
    drv_gps_nmea_reset(&nmea);
    assert(feed(&nmea, "$GNRMC,092750.000,A,5321.6802,S,00630.3372,W,0.02,31.66,280511,,,A*40\r\n"
                       "$GNGGA,092750.000,5321.6802,S,00630.3372,W,1,08,1.03,61.7,M,55.2,M,,*45\r\n") == 2);
    assert(feed(&nmea, "$GNGGA,,,,,,0,00,,,,,,,*78\r\n$GPRMC,092750.000,A,5321.6802,S,00630.3372,W,0.02,31."
                       "66,280511,,*33\r\n") == 2);
    assert(!nmea.fix.has_hdop && nmea.fix.hdop_e1 == 0);
    assert(nmea.fix.has_quality && nmea.fix.fix_quality == 0);
    assert(nmea.fix.mode == '\0');
}

int main(void)
{
    test_a_receiver_without_a_fix_says_so();
    test_a_fix_reads_as_signed_degrees();
    test_a_bad_checksum_changes_nothing();
    test_a_line_too_long_is_refused_and_the_next_one_reads();
    test_a_time_without_a_fix_reads_as_unix_seconds();
    test_a_fix_on_a_leap_day_reads_both();
    test_no_time_from_an_empty_early_or_impossible_field();
    test_satellites_in_view_add_up_over_systems();
    test_gga_gives_hdop_and_quality_and_rmc_its_mode();
    test_empty_gga_fields_and_an_old_rmc_report_nothing();
    puts("nmea: all passed");
    return 0;
}
